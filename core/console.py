"""Console output that cannot crash, on any terminal. Guide §9.1, §11.

The eval report and the books screen are full of characters a default Windows
console cannot encode: the rupee sign, box-drawing rules, em dashes. A cp1252
console raises `UnicodeEncodeError` on all of them, and the report dies
part-written — in front of whoever is watching.

Two defences, applied together:

1. `configure_stdout()` makes the stream UTF-8 with ``errors="replace"``, so
   encoding can never raise, and asks Windows to switch the console codepage to
   UTF-8 so the glyphs actually render.
2. If the stream still cannot represent the rupee sign, `unicode_ok()` reports
   False and the renderers fall back to ASCII. Degraded output beats a
   traceback, and "Rs.1,234.56" beats "?1,234.56".

This module holds no domain logic and imports nothing outside `core/` — only
`group_indian`, the digit-grouping rule, which it shares with `Money.__str__`
so the terminal and the UI cannot drift into two different notations. It
configures interpreter streams and chooses glyphs; it never writes anything
itself, which is why it can live in `core/` without breaking the no-I/O rule.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Final

from core.money import group_indian

RUPEE: Final = "₹"

#: Glyph pairs: (preferred, ASCII fallback).
_GLYPHS: Final = {
    "rupee": (RUPEE, "Rs."),
    "rule": ("─", "-"),  # box drawing horizontal
    "dash": ("—", "-"),  # em dash
    "arrow": ("→", "->"),
    "plusminus": ("±", "+/-"),
}

_unicode_ok: bool | None = None


def _stream_can_encode(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return False
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def configure_stdout() -> bool:
    """Make stdout as capable as this terminal allows. Safe to call repeatedly.

    Returns whether the rupee sign is safe to emit afterwards.
    """
    global _unicode_ok

    # Ask Windows for a UTF-8 console. Harmless and absent elsewhere.
    if sys.platform == "win32":
        # A console we cannot switch is not fatal — errors="replace" below
        # still guarantees no crash.
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # errors="replace" is the load-bearing part: it makes encoding
        # impossible to fail, whatever the terminal turns out to be. A stream
        # that refuses to reconfigure still works, just less prettily.
        with contextlib.suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")

    _unicode_ok = _stream_can_encode(RUPEE)
    return _unicode_ok


def unicode_ok() -> bool:
    """Whether the current stdout can represent the rupee sign."""
    if _unicode_ok is None:
        return _stream_can_encode(RUPEE)
    return _unicode_ok


def glyph(name: str) -> str:
    """The preferred character, or its ASCII stand-in on a limited terminal."""
    preferred, fallback = _GLYPHS[name]
    return preferred if unicode_ok() else fallback


def rule(width: int = 60) -> str:
    return glyph("rule") * width


def money(paise: int) -> str:
    """Format paise for display, in whatever the terminal can show.

    Mirrors `Money.__str__` but degrades to "Rs." rather than emitting a
    character the console will turn into a replacement mark.
    """
    sign = "-" if paise < 0 else ""
    rupees, remainder = divmod(abs(paise), 100)
    return f"{sign}{glyph('rupee')}{group_indian(rupees)}.{remainder:02d}"
