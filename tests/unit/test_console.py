"""Terminal-safe output. Guide §9.1, §11.

The eval report carries the rupee sign, box-drawing rules and em dashes. A
default Windows console is cp1252 and can encode none of them, so an unguarded
report dies part-written — during a demo, right before the benchmark screen.
"""

from __future__ import annotations

import io
import sys

import pytest

from core import console


class Cp1252Console(io.TextIOWrapper):
    """A default cmd.exe console that also refuses to be reconfigured."""

    def reconfigure(self, **kw: object) -> None:  # type: ignore[override]
        raise OSError("cannot reconfigure this console")


@pytest.fixture(autouse=True)
def _reset_cache():
    console._unicode_ok = None
    yield
    console._unicode_ok = None


def test_the_report_is_genuinely_unencodable_on_cp1252() -> None:
    """The risk is real, not theoretical — this is what we are defending."""
    with pytest.raises(UnicodeEncodeError):
        "₹1,234.56 ─── — →".encode("cp1252")


def test_a_cp1252_console_still_gets_a_complete_report() -> None:
    from pathlib import Path

    from eval.evaluate import score
    from eval.metrics import render

    real = sys.stdout
    sys.stdout = Cp1252Console(io.BytesIO(), encoding="cp1252", newline="",
                               write_through=True)
    try:
        console.configure_stdout()
        truth = {
            "generator_version": "1.0.0", "seed": 1, "scale": 1,
            "mappings": {"UTR-1": ["ORD-1"]}, "exceptions": [],
        }
        from core.config import Settings
        from core.run_result import RunResult

        text = render(score(truth, RunResult(), settings=Settings()))
        print(text)  # must not raise
        written = sys.stdout.buffer.getvalue()  # type: ignore[attr-defined]
    finally:
        sys.stdout = real

    assert written, "nothing was written"
    assert b"RECONCILIATION REPORT" in written
    assert b"Rs." in written, "should fall back to Rs. rather than a replacement mark"
    assert Path  # keep the import meaningful


def test_ascii_fallbacks_are_readable_not_replacement_marks() -> None:
    real = sys.stdout
    sys.stdout = Cp1252Console(io.BytesIO(), encoding="cp1252", newline="")
    try:
        console.configure_stdout()
        assert console.unicode_ok() is False
        assert console.glyph("rupee") == "Rs."
        assert console.rule(5) == "-----"
        assert console.money(781120) == "Rs.7,811.20"
        assert "?" not in console.money(781120)
    finally:
        sys.stdout = real


def test_a_utf8_console_keeps_the_real_glyphs() -> None:
    real = sys.stdout
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="")
    try:
        console.configure_stdout()
        assert console.unicode_ok() is True
        assert console.glyph("rupee") == "₹"
        assert console.money(781120) == "₹7,811.20"
    finally:
        sys.stdout = real


def test_money_helper_matches_the_domain_formatting() -> None:
    from core.money import Money

    real = sys.stdout
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="")
    try:
        console.configure_stdout()
        for paise in (0, 1, 100, 781120, -1250, 123456789):
            assert console.money(paise) == str(Money(paise))
    finally:
        sys.stdout = real


def test_configure_stdout_is_idempotent() -> None:
    assert console.configure_stdout() == console.configure_stdout()
