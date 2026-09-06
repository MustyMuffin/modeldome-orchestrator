"""Modeldome Orchestrator - run chess matches between language models.

Point it at any two models your API keys can reach and it plays them off
against each other, move by move, and records everything:

  * ``<out>/<match>.pgn``     - standard PGN, opens in Lichess or any engine
  * ``<out>/decisions.jsonl`` - one line per model call, holding the exact
    prompt sent and the exact text returned, plus tokens, cost, latency
    and status

This is the match engine behind The Modeldome (themodeldome.com). It is
the same board renderer and the same rules the published season ran
under, so a match you run here works exactly like a match you have
watched, minus the fancy video editing.

    pip install -r requirements.txt
    cp .env.example .env          # add your own keys
    python orchestrate.py --white anthropic/claude-opus-4-8 \
                          --black openai/gpt-5.5

Play a short series with colours alternating each game:

    python orchestrate.py --white openai/gpt-5.5 \
                          --black gemini/gemini-3.5-flash --games 4

Make it harder by withholding the legal-move list:

    python orchestrate.py --white ... --black ... --mode purist
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import chess
import chess.pgn
import litellm
from dotenv import load_dotenv
from observation import OBS_VERSION, render_v1

load_dotenv()

# Defaults match the settings the published season ran under, so an
# out-of-the-box match behaves exactly like a televised one. Every one is
# overridable on the command line.
MAX_TOKENS = 4096         # per model call
TURN_CAP = 200            # full moves before a game is called a draw
MODE = "assisted"         # "assisted" lists legal moves; "purist" does not
MAX_ATTEMPTS = 2          # then a legal move is forced, and logged as such

ACTION_INSTRUCTION = (
    'Respond with a JSON array of 1 or more actions and nothing else, in'
    ' exactly this form: [{"kind": "<action kind>", "params": {}}, ...].'
    " Actions are executed in order. End the array with"
    ' {"kind": "end_turn", "params": {}} to finish your turn. A single JSON'
    " object is also accepted. To move, use"
    ' {"kind": "move", "params": {"san": "<SAN>"}}.'
)


def ask(model: str, prompt: str, max_tokens: int = MAX_TOKENS) -> dict:
    """One completion. Returns the raw text plus usage, never raises."""
    started = time.perf_counter()
    try:
        resp = litellm.completion(
            model=model,
            max_tokens=max_tokens,
            timeout=300.0,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:                      # noqa: BLE001 - logged, not raised
        return {
            "text": "", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
            "latency_ms": (time.perf_counter() - started) * 1000.0,
            "status": "api_error", "error": f"{type(exc).__name__}: {exc}",
        }
    latency = (time.perf_counter() - started) * 1000.0
    try:
        cost = float(litellm.completion_cost(completion_response=resp))
    except Exception:                             # noqa: BLE001
        cost = 0.0
    usage = getattr(resp, "usage", None)
    return {
        "text": resp.choices[0].message.content or "",
        "tokens_in": int(getattr(usage, "prompt_tokens", 0) or 0),
        "tokens_out": int(getattr(usage, "completion_tokens", 0) or 0),
        "cost_usd": cost, "latency_ms": latency, "status": "ok", "error": None,
    }


def parse_move(text: str) -> str | None:
    """Pull the first move SAN out of a model reply, or None."""
    blob = re.search(r"\[.*]|\{.*}", text, re.S)
    if not blob:
        return None
    try:
        data = json.loads(blob.group(0))
    except json.JSONDecodeError:
        return None
    for action in data if isinstance(data, list) else [data]:
        if isinstance(action, dict) and action.get("kind") == "move":
            san = (action.get("params") or {}).get("san")
            if isinstance(san, str):
                return san
    return None


def play(
    white: str,
    black: str,
    out_dir: Path,
    *,
    mode: str = MODE,
    max_tokens: int = MAX_TOKENS,
    turn_cap: int = TURN_CAP,
    label: str = "game",
) -> dict:
    """Play one game. Returns a summary dict; appends to decisions.jsonl."""
    board = chess.Board()
    log_path = out_dir / "decisions.jsonl"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    forced = {white: 0, black: 0}
    spend = 0.0

    while not board.is_game_over() and board.fullmove_number <= turn_cap:
        model = white if board.turn == chess.WHITE else black
        prompt = render_v1(
            board, viewer_color=board.turn, mode=mode, is_my_move=True
        ) + "\n" + ACTION_INSTRUCTION

        chosen, rejected = None, []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            ask_text = prompt
            if rejected:
                ask_text += (
                    "\nREJECTED MOVES SINCE YOUR LAST OBSERVATION:\n"
                    + "\n".join(f"- {r}" for r in rejected)
                    + "\nDo not repeat these; they will be rejected again."
                )
            res = ask(model, ask_text, max_tokens)
            spend += res["cost_usd"]
            san = parse_move(res["text"])
            status = res["status"]
            if status == "ok":
                if san is None:
                    status = "parse_error"
                else:
                    try:
                        board.parse_san(san)
                        chosen, status = san, "ok"
                    except ValueError:
                        status = "invalid_output"
                        rejected.append(san)
            log.write(json.dumps({
                "game": label, "ply": board.ply(), "attempt": attempt, "model": model,
                "status": status, "san": san,
                "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"],
                "cost_usd": round(res["cost_usd"], 6),
                "latency_ms": round(res["latency_ms"], 1),
                "error": res["error"],
                "prompt": ask_text, "raw_output": res["text"],
            }) + "\n")
            log.flush()
            if chosen:
                break

        if chosen is None:
            # Same rule the season used: after MAX_ATTEMPTS the game does not
            # stall, a legal move is forced, and the fallback is recorded so
            # it can be counted against the model later.
            chosen = board.san(next(iter(board.legal_moves)))
            forced[model] += 1
            log.write(json.dumps({
                "game": label, "ply": board.ply(), "model": model, "status": "fallback",
                "san": chosen, "note": "forced legal move after failed attempts",
            }) + "\n")
            log.flush()

        print(f"  ply {board.ply():3d}  {model:34s} {chosen}")
        board.push_san(chosen)

    log.close()

    game = chess.pgn.Game()
    game.headers.update({
        "Event": "Modeldome Orchestrator", "Site": "local",
        "Date": datetime.now(UTC).strftime("%Y.%m.%d"),
        "White": white, "Black": black, "Result": board.result(),
        "Annotator": f"{OBS_VERSION} ({mode})",
    })
    node = game
    for mv in board.move_stack:
        node = node.add_variation(mv)
    pgn_path = out_dir / f"{label}.pgn"
    pgn_path.write_text(str(game) + "\n", encoding="utf-8")

    return {
        "label": label, "white": white, "black": black,
        "result": board.result(), "plies": board.ply(),
        "termination": _termination(board),
        "forced": dict(forced), "spend": spend, "pgn": pgn_path,
    }


def _termination(board: chess.Board) -> str:
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    if board.is_insufficient_material():
        return "insufficient material"
    if board.can_claim_threefold_repetition():
        return "repetition"
    if board.can_claim_fifty_moves():
        return "fifty-move rule"
    return "turn cap"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run chess matches between language models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--white", required=True,
                    help="litellm model id, e.g. openai/gpt-5.5")
    ap.add_argument("--black", required=True, help="litellm model id")
    ap.add_argument("--games", type=int, default=1,
                    help="games to play; colours alternate each game (default 1)")
    ap.add_argument("--mode", choices=("assisted", "purist"), default=MODE,
                    help="assisted lists every legal move each turn;"
                         " purist withholds it (default assisted)")
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS,
                    help=f"per model call (default {MAX_TOKENS})")
    ap.add_argument("--turn-cap", type=int, default=TURN_CAP,
                    help=f"full moves before a draw is declared (default {TURN_CAP})")
    ap.add_argument("-o", "--out", default="out", help="output directory")
    a = ap.parse_args()

    out = Path(a.out)
    print(f"{a.white}  vs  {a.black}")
    print(f"{a.games} game(s), {a.mode} mode, {a.max_tokens} tokens/call\n")

    results = []
    for i in range(1, a.games + 1):
        # Alternate colours so neither model keeps the first-move advantage.
        w, b = (a.white, a.black) if i % 2 else (a.black, a.white)
        print(f"--- game {i}: {w} (White) vs {b} (Black) ---")
        results.append(play(
            w, b, out, mode=a.mode, max_tokens=a.max_tokens,
            turn_cap=a.turn_cap, label=f"game{i}",
        ))
        print()

    print("=" * 66)
    score = {a.white: 0.0, a.black: 0.0}
    unfinished = 0
    for r in results:
        if r["result"] == "1-0":
            score[r["white"]] += 1
        elif r["result"] == "0-1":
            score[r["black"]] += 1
        elif r["result"] == "1/2-1/2":
            score[r["white"]] += 0.5
            score[r["black"]] += 0.5
        else:
            # PGN "*": stopped at the turn cap rather than reaching a real
            # ending. Not a draw, so it scores nothing for either side.
            unfinished += 1
        print(f"  {r['label']:6s} {r['result']:8s} {r['plies']:4d} plies"
              f"  {r['termination']:20s} {r['pgn'].name}")
    print("-" * 66)
    for model, pts in score.items():
        print(f"  {model:38s} {pts:5.1f}")
    total = sum(r["spend"] for r in results)
    forced = sum(sum(r["forced"].values()) for r in results)
    tail = f"   unfinished: {unfinished}" if unfinished else ""
    print(f"\n  spend ${total:.4f}   forced moves: {forced}{tail}")
    print(f"  logs: {out/'decisions.jsonl'}")


if __name__ == "__main__":
    main()
