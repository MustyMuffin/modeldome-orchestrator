# Modeldome Orchestrator

Run chess matches between large language models.

Point it at any two models your API keys can reach and it plays them off
against each other, turn by turn, and records everything they do. It is
the match engine behind [The Modeldome](https://themodeldome.com), so a
game you run here works exactly like a game from the show.

```bash
pip install -r requirements.txt
cp .env.example .env        # add keys for the providers you want
python orchestrate.py --white anthropic/claude-opus-4-8 --black openai/gpt-5.5
```

**The first command you run will pause for a while before printing
anything** - even `--help`. That is the `litellm` library loading, not the
tool hanging. It is slowest the very first time and quicker afterwards.

## What you get

Every run writes to `out/`:

* **`game1.pgn`** - standard PGN. Drop it into Lichess, chess.com or any
  engine and analyse the game like any other.
* **`decisions.jsonl`** - one line per model call, holding the **exact
  prompt sent** and the **exact text the model returned**, plus tokens in
  and out, cost, latency, and status.
* **`frames/game1/t0000.png` ...** - with `--frames`, one board image per
  ply, ready to assemble into video.

That second file is the interesting one. It is a complete record of the
model reasoning its way to a move, including the times it reasons its way
into a terrible one.

## Board frames

Pass `--frames` to also write a PNG of the position after every ply, into
`out/frames/<game>/t0000.png`, `t0001.png` and so on. The move just played
is highlighted and a king in check is marked, so the directory drops
straight into ffmpeg or an editor as an image sequence.

```bash
python orchestrate.py --white ... --black ... --frames
ffmpeg -framerate 2 -i out/frames/game1/t%04d.png game.mp4
```

This needs `cairosvg` plus the native libcairo library, neither of which
is installed by default:

```bash
pip install cairosvg
brew install cairo          # macOS
sudo apt install libcairo2  # Debian/Ubuntu
```

If they are missing, `--frames` says so and stops **before** any model
call is billed. Everything else works without them.

## Options

| Flag | Default | |
|---|---|---|
| `--white`, `--black` | required | any [litellm](https://docs.litellm.ai/docs/providers) model id |
| `--games N` | `1` | play a short series; colours alternate each game |
| `--mode` | `assisted` | `assisted` lists every legal move each turn, `purist` withholds it |
| `--max-tokens` | `4096` | per model call |
| `--turn-cap` | `200` | full moves before the game is called a draw |
| `--frames` | off | write a board PNG after every ply (see above) |
| `--frame-size` | `480` | frame edge in pixels |
| `-o`, `--out` | `out` | output directory |

A four-game series with colours alternating, so neither side keeps the
first-move advantage:

```bash
python orchestrate.py --white openai/gpt-5.5 \
                      --black gemini/gemini-3.5-flash --games 4
```

Purist mode is much harder. Without the legal-move list a model has to
derive legality from the board itself, and most of them are noticeably
worse at it:

```bash
python orchestrate.py --white ... --black ... --mode purist
```

## How a turn works

1. The board is rendered to text: an ASCII diagram, the FEN, castling and
   en-passant rights, the full move history, material balance, and (in
   assisted mode) every legal move.
2. That goes to the model, which replies with a JSON array of actions.
3. The move is checked against the rules. **An illegal or unparseable
   move is rejected and re-asked** - never silently corrected.
4. After two failed attempts a legal move is forced so the game cannot
   stall, and it is logged as `fallback` so it can be counted against
   that model afterwards.

Every attempt is written to the log, including the failures. Nothing is
hidden and nothing is cleaned up.

## Fairness

Both seats get the same renderer, the same token ceiling, and the same
information. The only thing that differs between them is which model is
behind it.

The board diagram is always drawn White-at-the-bottom for both players,
so neither gets a friendlier view. `observation.py` is versioned
(`obs_chess_v1`) and the version is stamped into every PGN, because
results are only comparable within a renderer version.

No engine is consulted while a game is being played. The published season
grades moves with Stockfish afterwards, but Stockfish never picks a move
and never appears in a model's prompt.

## Cost

You are paying your own providers. A single game is usually somewhere
between \$0.05 and \$2.00 depending on which models you pick; reasoning
models cost far more than small ones, and a long game costs more than a
short one. The end-of-run summary prints exactly what was spent.

## Requirements

Python 3.10+, an API key for at least one provider, and whatever those
providers charge. `.env` is gitignored.
