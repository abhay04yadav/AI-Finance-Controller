# Generator

Built at **gate 2**, before the agent. Guide §6.

`generate.py` is the exam paper **and** the answer key.

| Output | Who sees it |
|---|---|
| `ledger.csv`, `settlement.csv`, `bank.csv` | the agent |
| `truth.json` | only `eval/evaluate.py` |

## Injector registry

One row per injector, filled in at gate 2. Each must name the **real** Razorpay
or RBI behaviour it models and the §1.3 phase that describes it, per the gate 2
review question. An injector that cannot be traced to a documented behaviour is
an invented failure mode and tests nothing.

| Injector | Reason code | Ratio | Models | Guide ref | Source (Appendix C) |
|---|---|---|---|---|---|
| _pending gate 2_ | | | | | |

## Commands

```bash
python -m generator.generate --seed 42 --scale 50
python -m generator.generate --seed 42 --scale 5000
python -m generator.generate --seed 7  --scale 500   # held-out: never tune against this
```

## Determinism

Same seed + same scale must produce byte-identical output:

```bash
python -m generator.generate --seed 42 --scale 500 --out build/a
python -m generator.generate --seed 42 --scale 500 --out build/b
diff -r build/a build/b && echo "DETERMINISTIC"
```

## The check to do with your own eyes

```bash
grep -c "ORD-" data/seed42/bank.csv    # must be 0
```

If order IDs appear in the bank narration, the problem is fake, the agent scores
100%, and that 100% is worthless.
