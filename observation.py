"""Board-to-text rendering for chess observations (chess adapter spec
Section 5.2), versioned ``obs_chess_v1``.

Deterministic and small: a chess position is a FEN plus a short PGN, so
there is no token-ceiling problem and no priority-based truncation (the
hardest part of the FreeCiv renderer is near-free here).

Fairness rule: the board diagram is identical for both seats (always
White at the bottom); only point-of-view data differs (which color you
are, whose move it is). The legal-move list appears in ``assisted`` mode
and is withheld in ``purist`` mode. Version the renderer and record the
version in the match log; results only compare within a version.
"""

from __future__ import annotations

from collections.abc import Callable

import chess

OBS_VERSION = "obs_chess_v1"

_PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

# Shown to the seat whose turn it is NOT. Kept tiny on purpose: the
# orchestrator still calls every seat each loop turn, so the off-turn
# seat makes one trivially cheap model call that submits nothing.
WAIT_TEXT = (
    "It is not your move yet. Wait for your opponent to play.\n"
    'Reply with a single end_turn to pass: [{"kind": "end_turn", "params": {}}]'
)


def _labelled_board(board: chess.Board) -> str:
    """ASCII board with rank/file labels, always White at the bottom.

    Uppercase = White, lowercase = Black, '.' = empty. Identical for both
    seats (fairness)."""
    rows = str(board).split("\n")  # rank 8 first, files space-separated
    out = [f"{8 - i}  {row}" for i, row in enumerate(rows)]
    out.append("   a b c d e f g h")
    return "\n".join(out)


def _material(board: chess.Board) -> tuple[int, int]:
    """(white_points, black_points) by the standard 1/3/3/5/9 scale."""
    white = sum(
        _PIECE_VALUES[pt] * len(board.pieces(pt, chess.WHITE)) for pt in _PIECE_VALUES
    )
    black = sum(
        _PIECE_VALUES[pt] * len(board.pieces(pt, chess.BLACK)) for pt in _PIECE_VALUES
    )
    return white, black


def _pgn(board: chess.Board) -> str:
    """Full move history as SAN PGN movetext (small enough to never need
    truncation). Rebuilt from the root position so it is correct after a
    resume from an arbitrary start FEN."""
    replay = board.root()
    parts: list[str] = []
    for idx, move in enumerate(board.move_stack):
        san = replay.san(move)
        if replay.turn == chess.WHITE:
            parts.append(f"{replay.fullmove_number}. {san}")
        elif idx == 0:
            parts.append(f"{replay.fullmove_number}... {san}")
        else:
            parts.append(san)
        replay.push(move)
    return " ".join(parts) or "(no moves yet)"


def _rights(board: chess.Board) -> str:
    castling = board.castling_xfen() if board.castling_rights else "none"
    ep = chess.square_name(board.ep_square) if board.ep_square is not None else "none"
    return f"Castling rights: {castling}. En passant target: {ep}."


def render_v1(
    board: chess.Board,
    *,
    viewer_color: chess.Color,
    mode: str,
    is_my_move: bool,
    eval_line: str | None = None,
) -> str:
    """Render the position for one seat. ``eval_line`` is an optional
    previous-move Stockfish note (config include_eval, default off)."""
    if not is_my_move:
        return WAIT_TEXT

    color_name = "White" if viewer_color == chess.WHITE else "Black"
    stm = "White" if board.turn == chess.WHITE else "Black"
    white_pts, black_pts = _material(board)

    lines = [
        f"You are playing chess as {color_name}. It is your move ({stm} to play).",
        f"Move {board.fullmove_number} (ply {board.ply()}).",
        "",
        "Board (White uppercase, Black lowercase, White at the bottom):",
        _labelled_board(board),
        "",
        f"FEN: {board.fen()}",
        _rights(board),
        f"Move history: {_pgn(board)}",
        f"Material: White {white_pts}, Black {black_pts}"
        f" (balance {white_pts - black_pts:+d} for White).",
    ]
    if board.is_check():
        lines.append("You are in CHECK; your move must get out of check.")
    if eval_line:
        lines.append(eval_line)
    if mode == "assisted":
        legal = ", ".join(sorted(board.san(m) for m in board.legal_moves))
        lines.append(f"Your legal moves ({board.legal_moves.count()}): {legal}")
    return "\n".join(lines)


Renderer = Callable[..., str]

RENDERERS: dict[str, Renderer] = {OBS_VERSION: render_v1}
