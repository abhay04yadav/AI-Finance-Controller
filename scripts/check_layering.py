"""Layering check — guide §3.2. Enforced, not aspirational.

    core/                     imports NOTHING from this project
    matching/ ingest/ posting/ exceptions_/ adjudication/
                              import only core/
    pipeline/                 wires them
    api/ web/                 delivery mechanisms, no business logic

Single implementation, called by scripts/check_layering.sh, make layer-check,
scripts/drift_check.py, and CI. Reads CODE ONLY — an import named in a docstring
is documentation, not a violation.

    python scripts/check_layering.py     -> exit 0 clean, 1 on violation
"""

from __future__ import annotations

import ast
import contextlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache",
             ".ruff_cache", ".mypy_cache", "build"}

PROJECT_PKGS = {"core", "ingest", "matching", "adjudication", "posting", "exceptions_",
                "pipeline", "api", "persistence", "generator", "eval"}

# package -> what it is allowed to import from this project
ALLOWED: dict[str, set[str]] = {
    "core": set(),
    "ingest": {"core"},
    "matching": {"core"},
    "posting": {"core"},
    "exceptions_": {"core", "posting"},
    "adjudication": {"core"},
    # pipeline/ "wires them" (§3.2), and the journal repository is one of the
    # things it wires. The domain still never sees SQL: posting/ and core/ hold
    # only the JournalRepository protocol, and the concrete store is injected.
    "pipeline": {
        "core",
        "ingest",
        "matching",
        "adjudication",
        "posting",
        "exceptions_",
        "persistence",
    },
    "generator": {"core"},
    # eval/ may reach the generator: §6.3 requires it to refuse a dataset built
    # by a different major version, and §7.5's multi-seed and held-out-seed runs
    # need dataset preparation. This does NOT weaken the gate 3 stop condition,
    # which is about the AGENT's internals (matching/adjudication/posting/ingest)
    # — the generator is not the agent, and scoring itself stays pure.
    "eval": {"core", "pipeline", "generator"},
}


def imported_roots(path: Path) -> set[tuple[str, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    out: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add((a.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add((node.module.split(".")[0], node.lineno))
    return out


def main() -> int:
    # Windows console defaults to cp1252 and cannot encode the guide's punctuation.
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

    violations: list[str] = []
    for pkg, allowed in ALLOWED.items():
        base = ROOT / pkg
        if not base.exists():
            continue
        for f in sorted(base.rglob("*.py")):
            if SKIP_DIRS & set(f.relative_to(ROOT).parts):
                continue
            for root, lineno in sorted(imported_roots(f)):
                if root in PROJECT_PKGS and root != pkg and root not in allowed:
                    rel = f.relative_to(ROOT).as_posix()
                    violations.append(f"  {rel}:{lineno}: {pkg}/ may not import {root}/")

    if violations:
        print("LAYERING VIOLATION (guide section 3.2)")
        print("\n".join(sorted(set(violations))))
        return 1
    print("LAYERING OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
