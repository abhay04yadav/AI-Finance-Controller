"""Makefile-equivalent runner for machines without GNU make.

    python tasks.py help
    python tasks.py generate --seed 42 --scale 5000
    python tasks.py eval --no-llm

The Makefile stays the canonical interface — the Review Guide's gates call
`make eval`, `make generate`, etc. This exists because GNU make is not on PATH
on this Windows box (MinGW's `mingw32-make` works, and so does this).
"""

from __future__ import annotations

import argparse
import subprocess
import sys

PY = sys.executable

HELP = """AI Finance Controller

  setup         install dependencies (editable + dev extras)
  generate      build a seeded synthetic dataset      [gate 2]
  match         run the reconciliation pipeline       [gate 8]
  eval          score the agent against truth.json    [gate 3]
  demo          generate + match + eval at demo scale [gate 14]

  test          pytest
  lint          ruff
  typecheck     mypy
  layer-check   section 3.2 dependency rule
  drift-check   the six Review Guide part 3 checks
  tree          print the repo structure
"""


def run(*cmd: str) -> int:
    print("$", " ".join(cmd))
    return subprocess.call(list(cmd))


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
    if t == "setup":
        return run(PY, "-m", "pip", "install", "-e", ".[dev]")
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
