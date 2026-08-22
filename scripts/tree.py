"""Minimal `tree -L 2` for the gate 0 check, since Windows has no tree(1).

Forces UTF-8 on stdout: the default Windows console codepage is cp1252 and
cannot encode box-drawing characters (or the rupee sign, which the eval report
will need at gate 3). Falls back to ASCII connectors if that fails.
"""

import sys
from pathlib import Path

IGNORE = {
    "__pycache__",
    "node_modules",
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".egg-info",
}

try:
    sys.stdout.reconfigure(encoding="utf-8")
    TEE, ELL, BAR, GAP = "├── ", "└── ", "│   ", "    "
except Exception:
    TEE, ELL, BAR, GAP = "|-- ", "`-- ", "|   ", "    "


def visible(p: Path) -> bool:
    if p.name in IGNORE or p.name.endswith(".egg-info"):
        return False
    # show .github and .env.example, hide the rest of the dotfiles
    return not p.name.startswith(".") or p.name in {".github", ".env.example", ".gitignore"}


def walk(d: Path, depth: int, max_depth: int, prefix: str = "") -> None:
    if depth > max_depth:
        return
    kids = sorted(
        (p for p in d.iterdir() if visible(p)),
        key=lambda p: (p.is_file(), p.name.lower()),
    )
    for i, p in enumerate(kids):
        last = i == len(kids) - 1
        print(f"{prefix}{ELL if last else TEE}{p.name}{'/' if p.is_dir() else ''}")
        if p.is_dir():
            walk(p, depth + 1, max_depth, prefix + (GAP if last else BAR))


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    max_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    print(f"{root.resolve().name}/")
    walk(root, 1, max_depth)
