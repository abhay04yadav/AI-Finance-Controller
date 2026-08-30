"""Makefile-equivalent runner for machines without GNU make.

    python tasks.py help
    python tasks.py generate --seed 42 --scale 5000
    python tasks.py eval --no-llm

Every target runs inside the project virtualenv at .venv/ if it exists, whether
or not you activated it — same as the Makefile.

The Makefile stays the canonical interface: the Review Guide's gates call
`make eval`, `make generate`, etc. This exists because GNU make is not on PATH
on this Windows box (MinGW's `mingw32-make` works, and so does this).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _interpreter() -> str:
    """The project venv if it exists, else whatever is running us.

    Means `python tasks.py test` uses .venv/ without an activate step, and
    matches the Makefile so both entry points cannot drift apart.
    """
    for rel in ("Scripts/python.exe", "bin/python"):
        candidate = ROOT / ".venv" / rel
        if candidate.exists():
            return str(candidate)
    return sys.executable


PY = _interpreter()

HELP = """AI Finance Controller

  venv          create the project virtualenv at .venv/
  setup         venv + install dependencies (editable + dev extras)
  generate      build a seeded synthetic dataset      [gate 2]
  match         run the reconciliation pipeline       [gate 8]
  eval          score the agent against truth.json    [gate 3]
  demo          generate + match + eval at demo scale [gate 14]

  api           serve the API on :8000                [gate 12]
  web           serve the screens on :3000            [gate 12]

  test          pytest
  lint          ruff
  typecheck     mypy
  layer-check   section 3.2 dependency rule
  drift-check   the six Review Guide part 3 checks
  tree          print the repo structure
"""


def run(*cmd: str, cwd: str | None = None) -> int:
    print("$", " ".join(cmd), f"   (in {cwd})" if cwd else "")
    # shell=True on Windows only: npm is npm.cmd there, and subprocess will
    # not resolve it otherwise.
    shell = cwd is not None and sys.platform == "win32"
    if shell:
        return subprocess.call(" ".join(cmd), cwd=cwd, shell=True)
    return subprocess.call(list(cmd), cwd=cwd)


def make_venv() -> int:
    rc = run(sys.executable, "-m", "venv", str(ROOT / ".venv"))
    return rc or run(_interpreter(), "-m", "pip", "install", "--upgrade", "pip")


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("target", nargs="?", default="help")
    ap.add_argument("--seed", default="42")
    ap.add_argument("--scale", default="500")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()

    data = f"data/seed{a.seed}"
    t = a.target

    if t == "help":
        print(HELP)
        return 0
    if t == "venv":
        return make_venv()
    if t == "setup":
        rc = make_venv()
        return rc or run(_interpreter(), "-m", "pip", "install", "-e", ".[dev]")
    if t == "generate":
        return run(PY, "-m", "generator.generate",
                   "--seed", a.seed, "--scale", a.scale, "--out", data)
    if t == "match":
        return run(PY, "-m", "pipeline.orchestrator", "--dataset", data)
    if t == "eval":
        cmd = [PY, "-m", "eval.evaluate", "--dataset", data, "--seed", a.seed, "--scale", a.scale]
        if a.no_llm:
            cmd.append("--no-llm")
        if a.seeds:
            cmd += ["--seeds", a.seeds]
        return run(*cmd)
    if t == "demo":
        for step in ("generate", "match", "eval"):
            rc = run(PY, "tasks.py", step, "--seed", a.seed, "--scale", "5000")
            if rc:
                return rc
        return 0
    # The two long-running processes. Present here and not only in the
    # Makefile because this runner is documented as the equivalent fallback,
    # and a fallback that is missing targets is not equivalent — a reader on
    # Windows following the README would have hit "unknown target: api".
    if t == "api":
        return run(PY, "-m", "uvicorn", "api.main:app",
                   "--host", "127.0.0.1", "--port", "8000", "--reload")
    if t == "web":
        return run("npm", "run", "dev", cwd="web")

    if t == "test":
        return run(PY, "-m", "pytest", "tests/", "-v")
    if t == "lint":
        return run(PY, "-m", "ruff", "check", ".")
    if t == "typecheck":
        return run(PY, "-m", "mypy", "core/", "matching/", "ingest/", "posting/")
    if t == "layer-check":
        return run("bash", "scripts/check_layering.sh")
    if t == "drift-check":
        return run("bash", "scripts/drift_check.sh")
    if t == "tree":
        return run(PY, "scripts/tree.py", ".", "2")

    print(f"unknown target: {t}\n")
    print(HELP)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
