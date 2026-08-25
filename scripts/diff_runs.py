"""Compare two eval reports byte for byte. Review Guide gate 11.

    python scripts/diff_runs.py .run1.txt .run2.txt     (or: make determinism)

`diff` exists on this machine only through Git Bash, and the Review Guide's
`diff /tmp/run1.txt /tmp/run2.txt` does not run in PowerShell at all. This is
the same check with no shell assumptions, and it prints the first differing line
rather than the whole file — when two runs disagree, the first divergence is the
one that tells you what is non-deterministic.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

    if len(argv) != 2:
        print("usage: python scripts/diff_runs.py RUN1 RUN2")
        return 2

    left, right = (Path(a) for a in argv)
    for path in (left, right):
        if not path.exists():
            print(f"missing: {path}")
            return 2

    a = left.read_text(encoding="utf-8").splitlines()
    b = right.read_text(encoding="utf-8").splitlines()

    for lineno, (x, y) in enumerate(zip(a, b, strict=False), 1):
        if x != y:
            print("NOT DETERMINISTIC — the same seed produced two different runs")
            print(f"  {left}:{lineno}: {x}")
            print(f"  {right}:{lineno}: {y}")
            return 1

    if len(a) != len(b):
        print("NOT DETERMINISTIC — the two runs are different lengths")
        print(f"  {left}: {len(a)} lines")
        print(f"  {right}: {len(b)} lines")
        return 1

    print(f"DETERMINISTIC — {len(a)} lines, byte-identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
