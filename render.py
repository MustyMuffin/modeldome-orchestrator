"""Board-to-PNG rendering.

python-chess exports an SVG per position; cairosvg rasterises it to the
numbered frames (``t0001.png``, ``t0002.png``, ...) that a timelapse or
video edit consumes directly. Frames are written after each ply with the
move just played highlighted, and the king marked when it is in check.

cairosvg is OPTIONAL and imported lazily, so the tool installs and runs
fine without it - you only need it if you pass --frames.

    pip install cairosvg

cairosvg needs the native libcairo library as well. On macOS that is
``brew install cairo``; on Debian/Ubuntu ``apt install libcairo2``.
Homebrew installs it somewhere Python's loader does not search by
default, so ``_ensure_cairo_findable`` below teaches ctypes where to look
rather than making you export DYLD_LIBRARY_PATH.
"""

from __future__ import annotations

import ctypes.util
import glob
from pathlib import Path

import chess
import chess.svg

DEFAULT_SIZE = 480

_CAIRO_PATCHED = False
# Where libcairo actually lives when ctypes.util.find_library misses it:
# Homebrew (Apple Silicon, then Intel), then the usual Linux locations.
_LIB_DIRS = (
    "/opt/homebrew/lib",
    "/usr/local/lib",
    "/usr/lib",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib64",
)


def _ensure_cairo_findable() -> None:
    """Make ``ctypes.util.find_library('cairo')`` resolve on Homebrew
    macOS, and stay correct on Linux. Idempotent, and a no-op wherever
    the loader already finds the library."""
    global _CAIRO_PATCHED
    if _CAIRO_PATCHED:
        return
    _CAIRO_PATCHED = True
    original = ctypes.util.find_library

    def patched(name: str) -> str | None:
        found = original(name)
        if found:
            return found
        for base in _LIB_DIRS:
            for pattern in (
                f"{base}/lib{name}.*.dylib",
                f"{base}/lib{name}.dylib",
                f"{base}/lib{name}.so.*",
                f"{base}/lib{name}.so",
            ):
                hits = sorted(glob.glob(pattern))
                if hits:
                    return hits[0]
        return None

    ctypes.util.find_library = patched


class RenderUnavailable(RuntimeError):
    """cairosvg or libcairo is missing; frames cannot be written."""


def _cairosvg():
    _ensure_cairo_findable()
    try:
        import cairosvg
    except Exception as exc:  # noqa: BLE001 - surfaced as a clear message
        raise RenderUnavailable(
            "--frames needs cairosvg and the native libcairo library.\n"
            "  pip install cairosvg\n"
            "  macOS:  brew install cairo\n"
            "  Debian: sudo apt install libcairo2\n"
            f"(import failed: {type(exc).__name__}: {exc})"
        ) from exc
    return cairosvg


def board_svg(
    board: chess.Board,
    *,
    lastmove: chess.Move | None = None,
    size: int = DEFAULT_SIZE,
    orientation: chess.Color = chess.WHITE,
) -> str:
    """SVG for a position, highlighting the move just played and marking
    the king of the side to move when it is in check."""
    check = board.king(board.turn) if board.is_check() else None
    return chess.svg.board(
        board,
        lastmove=lastmove,
        check=check,
        size=size,
        orientation=orientation,
        coordinates=True,
    )


def board_to_png(
    board: chess.Board,
    path: str | Path,
    *,
    lastmove: chess.Move | None = None,
    size: int = DEFAULT_SIZE,
    orientation: chess.Color = chess.WHITE,
) -> Path:
    """Render a position to a PNG and return the path, creating parent
    directories. Raises RenderUnavailable if cairosvg is missing."""
    cairosvg = _cairosvg()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(
        bytestring=board_svg(
            board, lastmove=lastmove, size=size, orientation=orientation
        ).encode("utf-8"),
        write_to=str(out),
        output_width=size,
        output_height=size,
    )
    return out
