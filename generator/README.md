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

| Reason code | Rate | Real behaviour modelled | Source |
|---|---|---|---|
| `AWAITING_SETTLEMENT` | 0.8% of orders | Partial settlement, whole transactions only | Razorpay *About Settlements* / §1.3 phase 5 |
| `AUTO_REFUNDED` | 0.4% of orders | 3-day capture-or-auto-refund | Razorpay *Payment Capture Settings* / §1.3 phase 3 |
| `LATE_AUTHORIZATION` | 0.6% of orders | Failed-then-authorized via 3-day bank polling | Razorpay *Late Payment Authorisations* / §1.3 phase 3 |
| `CROSS_PERIOD_REFUND` | 0.8% of orders | Refunds netted from the clearing settlement | Razorpay *Settlements* / §1.4 reason 4 |
| `HOLIDAY_SHIFT` | 8.0% of settlements | T+2 working-day cycle, weekend and holiday exclusions | Razorpay *Settlements FAQs* / §1.3 phase 5 |
| `ROUNDING_DRIFT` | 8.0% of settlements | Per-step fee and GST rounding in the settlement report | Razorpay *Settlements* / §4.2 |
| `MISSING_IN_LEDGER` | 4.0% of settlements | Unrecorded revenue | gateway settled a sale the ledger never captured / §1.5 |
| `DUPLICATE_UTR` | 4.0% of settlements | One UTR per settlement from the banking partner | Razorpay *Settlements* / §1.3 phase 5 |

**Rates are per-unit, and the unit matters.** §6.2 states every ratio as a share
of "records", which is the right denominator for a ledger-row anomaly but not for
a bank-row one: a duplicated UTR is a property of a settlement, not of the 42
orders inside it. Applying an order ratio to a settlement-level mode overflows
the batch pool the moment orders-per-batch grows — at `--scale 5000` it silently
starved four of the eight injectors to zero. Each injector now declares its
`unit`, and `validate.py` asserts all eight modes are present in every dataset.

A floor of one instance applies at every scale, so all eight modes are exercised
even at the 50-record bar the brief sets, where 0.4% would round to zero. Above
roughly 250 records the ratios dominate entirely.

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
