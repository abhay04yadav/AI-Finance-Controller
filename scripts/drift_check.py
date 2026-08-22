"""Drift check — the six standing checks from the Review Guide, part 3.

Run every few hours. Catches slow decay that gate-by-gate review misses.

    python scripts/drift_check.py        (or: make drift-check)

Why this is not the raw grep from the Review Guide: grep matches the rules as
written in docstrings as readily as it matches real violations, and a check that
cries wolf gets ignored. This tokenizes each file and looks at CODE ONLY,
skipping strings and comments. The raw greps still work for a manual spot-check;
this is what CI runs.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache",
             ".ruff_cache", ".mypy_cache", "web", "build"}

_SKIP_TOK = {tokenize.STRING, tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
             tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):  # py3.12+
    if hasattr(tokenize, _name):
        _SKIP_TOK.add(getattr(tokenize, _name))


def py_files(*roots: str) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        base = ROOT / r
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if SKIP_DIRS & set(p.relative_to(ROOT).parts):
                continue
            out.append(p)
    return sorted(out)


def code_lines(path: Path) -> list[tuple[int, str]]:
    """(lineno, code text) with strings and comments removed."""
    src = path.read_text(encoding="utf-8")
    buf: dict[int, list[str]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in _SKIP_TOK:
                continue
            buf.setdefault(tok.start[0], []).append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Better a false positive than a missed violation.
        return list(enumerate(src.splitlines(), 1))
    return sorted((n, " ".join(v)) for n, v in buf.items())


def scan(files: list[Path], pattern: str, exclude: str | None = None) -> list[str]:
    rx, ex = re.compile(pattern), re.compile(exclude) if exclude else None
    hits = []
    for p in files:
        for n, text in code_lines(p):
            if rx.search(text) and not (ex and ex.search(text)):
                hits.append(f"  {p.relative_to(ROOT).as_posix()}:{n}: {text.strip()}")
    return hits


def report(n: int, title: str, hits: list[str], flag: str) -> bool:
    print(f"{n}. {title}")
    if hits:
        print("\n".join(hits))
        print(f"  >> {flag}")
        return False
    print("  ok")
    return True


def main() -> int:
    # Windows console defaults to cp1252 and cannot encode the guide's punctuation.
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

    ok = True

    # 1 — money path is int paise, always (§2.7 rule 1)
    ok &= report(1, "No floats in the money path",
                 scan(py_files("core", "matching", "posting", "ingest"), r"\bfloat\s*\("),
                 "FLOATS FOUND")

    # 2 — no wall clock in business logic (§9.2); inject a Clock
    ok &= report(2, "No wall clock in business logic",
                 scan(py_files("core", "matching", "posting", "ingest", "generator"),
                      r"\b(date\s*\.\s*today|datetime\s*\.\s*(now|today)|time\s*\.\s*time)\s*\(",
                      exclude=r"\bdef\s+today\b"),
                 "CLOCK LEAK")

    # 3 — never swallow (§5.5)
    ok &= report(3, "No swallowed errors",
                 scan(py_files("core", "matching", "posting", "ingest", "adjudication",
                               "pipeline", "api", "persistence", "generator", "eval",
                               "exceptions_"),
                      r"except[^:]*:\s*pass\b"),
                 "SWALLOWED ERROR")

    # 4 — layering (§3.2)
    print("4. Layering intact")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_layering.py")],
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        print(proc.stdout.rstrip())
        print("  >> LAYERING VIOLATION")
        ok = False
    else:
        print("  ok")

    # 5 — L4 is gate 11 of 14 (§10). No LLM client outside adjudication/ before then.
    ok &= report(5, "No LLM client outside adjudication/ before gate 11",
                 scan(py_files("core", "matching", "posting", "ingest", "pipeline",
                               "api", "persistence", "generator", "eval", "exceptions_"),
                      r"\banthropic\b|messages\s*\.\s*create"),
                 "LLM LEAKED OUT OF adjudication/")

    # 6 — the check to do with your own eyes (§6.2 step 4)
    print("6. Bank narration still clean (must be 0)")
    found = False
    for bank in sorted(ROOT.glob("data/*/bank.csv")):
        found = True
        n = bank.read_text(encoding="utf-8").count("ORD-")
        print(f"  {bank.relative_to(ROOT).as_posix()}: {n}")
        if n:
            print("  >> ORDER IDS LEAKED INTO BANK NARRATION — every score is meaningless")
            ok = False
    if not found:
        print("  skipped (no dataset yet — gate 2)")

    print()
    print("DRIFT CHECK CLEAN" if ok else "DRIFT CHECK FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
