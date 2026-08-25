# AI Finance Controller — task runner. Guide §3.2.
#
# Every target runs inside the project virtualenv at .venv/ if it exists, so you
# never have to remember to activate it. `make venv` creates it.
#
# Windows note: GNU `make` is not on PATH here, but MinGW ships `mingw32-make`.
# Use `mingw32-make <target>`, or `alias make=mingw32-make` in your shell.
# `python tasks.py <target>` is an equivalent fallback that needs no make at all.

VENV := .venv
ifeq ($(OS),Windows_NT)
  VENV_PY := $(VENV)/Scripts/python.exe
else
  VENV_PY := $(VENV)/bin/python
endif

# Prefer the venv interpreter; fall back to whatever `python` is on PATH.
PY      ?= $(if $(wildcard $(VENV_PY)),$(VENV_PY),python)

SEED    ?= 42
SCALE   ?= 500
SEEDS   ?=
NO_LLM  ?=
DATA    ?= data/seed$(SEED)

.PHONY: help venv setup generate fee-datasets match eval determinism demo test lint typecheck layer-check drift-check tree clean

help:
	@echo "AI Finance Controller"
	@echo ""
	@echo "  make venv          create the project virtualenv at .venv/"
	@echo "  make setup         venv + install dependencies (editable + dev extras)"
	@echo ""
	@echo "  make generate      build a seeded synthetic dataset      [gate 2]"
	@echo "  make fee-datasets  non-round MDR fixtures for L2         [gate 6]"
	@echo "  make match         run the reconciliation pipeline       [gate 8]"
	@echo "  make eval          score the agent against truth.json    [gate 3]"
	@echo "  make determinism   two runs of make eval must be identical [gate 11]"
	@echo "  make demo          generate + match + eval, demo scale   [gate 14]"
	@echo ""
	@echo "  make test          pytest"
	@echo "  make lint          ruff"
	@echo "  make typecheck     mypy"
	@echo "  make layer-check   section 3.2 dependency rule"
	@echo "  make drift-check   the six Review Guide part 3 checks"
	@echo "  make tree          print the repo structure (no tree(1) on Windows)"
	@echo ""
	@echo "  Interpreter: $(PY)"
	@echo "  Vars: SEED=$(SEED) SCALE=$(SCALE) SEEDS= NO_LLM=1"

venv:
	python -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip

setup: venv
	$(VENV_PY) -m pip install -e ".[dev]"

generate:
	$(PY) -m generator.generate --seed $(SEED) --scale $(SCALE) --out $(DATA)

# Fee-model fixtures for gate 6. The default 2.00% dataset CANNOT validate L2:
# it is the same value L2 falls back to, so a fee model that never runs would
# still appear to recover it. These plant non-round rates and a second MDR slab.
fee-datasets:
	$(PY) -m generator.generate --seed $(SEED) --scale $(SCALE) --fee-rate 0.0175 --out data/fee0175
	$(PY) -m generator.generate --seed $(SEED) --scale $(SCALE) --fee-rate 0.0235 --out data/fee0235
	$(PY) -m generator.generate --seed $(SEED) --scale $(SCALE) --fee-rate 0.0175 --intl-ratio 0.10 --out data/feeslab

match:
	$(PY) -m pipeline.orchestrator --dataset $(DATA)

eval:
	$(PY) -m eval.evaluate --dataset $(DATA) --seed $(SEED) --scale $(SCALE) \
	  $(if $(NO_LLM),--no-llm,) $(if $(SEEDS),--seeds $(SEEDS),)

# Gate 11's stop condition: two runs of the same seed must be byte-identical.
# --no-timing drops throughput, which measures the machine rather than the
# system and is the one line that legitimately differs between runs.
determinism:
	$(PY) -m eval.evaluate --dataset $(DATA) --seed $(SEED) --scale $(SCALE) --no-timing > .run1.txt
	$(PY) -m eval.evaluate --dataset $(DATA) --seed $(SEED) --scale $(SCALE) --no-timing > .run2.txt
	$(PY) scripts/diff_runs.py .run1.txt .run2.txt

demo:
	$(MAKE) generate SCALE=5000
	$(MAKE) match SCALE=5000
	$(MAKE) eval SCALE=5000

test:
	$(PY) -m pytest tests/ -v

lint:
	$(PY) -m ruff check .

typecheck:
	$(PY) -m mypy core/ matching/ ingest/ posting/ adjudication/ exceptions_/ pipeline/

layer-check:
	$(PY) scripts/check_layering.py

drift-check:
	$(PY) scripts/drift_check.py

tree:
	$(PY) scripts/tree.py . 2

clean:
	$(PY) -c "import shutil,pathlib;[shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
