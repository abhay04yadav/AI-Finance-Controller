# AI Finance Controller — task runner. Guide §3.2.
#
# Windows note: GNU `make` is not on PATH here, but MinGW ships `mingw32-make`.
# Use `mingw32-make <target>`, or `alias make=mingw32-make` in your shell.
# `python tasks.py <target>` is an equivalent fallback that needs no make at all.

PY      ?= python
SEED    ?= 42
SCALE   ?= 500
SEEDS   ?=
NO_LLM  ?=
DATA    ?= data/seed$(SEED)

.PHONY: help setup generate match eval demo test lint typecheck layer-check drift-check tree clean

help:
	@echo "AI Finance Controller"
	@echo ""
	@echo "  make setup         install dependencies (editable + dev extras)"
	@echo "  make generate      build a seeded synthetic dataset      [gate 2]"
	@echo "  make match         run the reconciliation pipeline       [gate 8]"
	@echo "  make eval          score the agent against truth.json    [gate 3]"
	@echo "  make demo          generate + match + eval, demo scale   [gate 14]"
	@echo ""
	@echo "  make test          pytest"
	@echo "  make lint          ruff"
	@echo "  make typecheck     mypy"
	@echo "  make layer-check   section 3.2 dependency rule"
	@echo "  make drift-check   the six Review Guide part 3 checks"
	@echo "  make tree          print the repo structure (no tree(1) on Windows)"
	@echo ""
	@echo "  Vars: SEED=$(SEED) SCALE=$(SCALE) SEEDS= NO_LLM=1"

setup:
	$(PY) -m pip install -e ".[dev]"

generate:
	$(PY) -m generator.generate --seed $(SEED) --scale $(SCALE) --out $(DATA)

match:
	$(PY) -m pipeline.orchestrator --dataset $(DATA)

eval:
	$(PY) -m eval.evaluate --dataset $(DATA) --seed $(SEED) --scale $(SCALE) \
	  $(if $(NO_LLM),--no-llm,) $(if $(SEEDS),--seeds $(SEEDS),)

demo:
	$(MAKE) generate SCALE=5000
	$(MAKE) match SCALE=5000
	$(MAKE) eval SCALE=5000

test:
	$(PY) -m pytest tests/ -v

lint:
	$(PY) -m ruff check .

typecheck:
	$(PY) -m mypy core/ matching/ ingest/ posting/

layer-check:
	$(PY) scripts/check_layering.py

drift-check:
	$(PY) scripts/drift_check.py

tree:
	$(PY) scripts/tree.py . 2

clean:
	$(PY) -c "import shutil,pathlib;[shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
