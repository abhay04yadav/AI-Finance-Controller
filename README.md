# AI Finance Controller

**Razorpay /buildathon — Track 04: Run the books and the cash position**

> A reconciliation tool matches records. A controller closes the books.

Three parties hold three different records of the same money — the merchant's
ledger says ₹8,000 was sold, the gateway's settlement report says ₹160 fee and
₹28.80 GST were deducted, and the bank statement says ₹7,811.20 landed under a
narration reading `NEFT RAZORPAYSETL88 CR`. No shared key joins them, the
cardinality is many-to-one, the amounts are transformed by a merchant-specific
MDR nobody can state, the dates are shifted by a T+2 working-day cycle, and last
week's refunds are netted out of this week's credit. That is why this is still
done by hand in Excel.

This system closes that loop end to end: ingest → match → classify → decide →
**post the double-entry journal** → report the cash position. It is
deterministic where it can be, probabilistic only where it must be, and measured
everywhere.

---

## Status

**Gate 0 of 14 — repo skeleton.** Every module below is a documented stub. No
business logic is implemented yet, by design: the build order in §10 of the
implementation guide is deliberate and is being followed one gate at a time.

| Gate | What | Pass condition | Done |
|---|---|---|---|
| 0 | Repo skeleton | Structure matches §3.2 | ☑ |
| 1 | Core domain | `Money(10.5)` raises `TypeError` | ☐ |
| 2 | Generator ⭐ | Deterministic; **0** order IDs in `bank.csv` | ☐ |
| 3 | Eval harness ⭐ | Exact-set-equality scoring | ☐ |
| 4 | L0 ingest | Totals tie; idempotent | ☐ |
| 5 | L1 exact matcher ⭐⭐ | **Precision exactly 100%**, coverage 60–90% | ☐ |
| 6 | L2 fee model | Rate within 0.1%; median not mean | ☐ |
| 7 | L3 subset matcher | Property tests; pool < 12; < 100 ms | ☐ |
| 8 | L5 posting | Entries balance; books tie; idempotent | ☐ |
| 9 | Exceptions + actions | WHAT / WHY / ACTION on every card | ☐ |
| 10 | **No-LLM checkpoint ⭐⭐⭐** | **94% rate, ≥98% precision, 0 LLM calls** | ☐ |
| 11 | L4 LLM adjudication | < 10% budget; deterministic; guardrails pass | ☐ |
| 12 | UI | Exceptions is home; entry preview in review | ☐ |
| 13 | Benchmark screen | Runs live; surfaces its own miss | ☐ |
| 14 | Final | Clean clone < 5 min; held-out seed holds | ☐ |

Metrics table, source references, and the Limitations section land at gate 14.
They are not written in advance, because there is nothing honest to put in them yet.

---

## Quickstart

The project runs in its own virtualenv at `.venv/`. One command creates it and
installs everything:

```bash
make setup
```

Then:

```bash
make generate SEED=42 SCALE=500      # gate 2
make eval     SEED=42 SCALE=500      # gate 3
```

Every `make` target uses `.venv/` automatically — **there is no activate step**.
Nothing is installed into your global Python, and the LLM client is not
installed at all until gate 11 (`pip install -e ".[llm]"`), so gates 0–10 and
the `--no-llm` ablation provably run without it.

**Windows:** GNU `make` is not on PATH on the build machine. Use MinGW's
`mingw32-make <target>`, or the equivalent no-make runner, which picks up
`.venv/` the same way:

```bash
python tasks.py setup
python tasks.py generate --seed 42 --scale 500
python tasks.py eval --seed 42 --scale 500 --no-llm
```

To work in the venv directly:

```bash
source .venv/Scripts/activate     # Git Bash on Windows
source .venv/bin/activate         # macOS / Linux
.venv\Scripts\Activate.ps1        # PowerShell
```

Run `make help` (or `python tasks.py help`) for every target; it prints which
interpreter it resolved to.

---

## Architecture (§3.1)

```
              ledger.csv   settlement.csv   bank.csv
                   └─────────────┼─────────────┘
                                 ▼
  L0  INGEST & NORMALIZE      3 sources → one canonical Record
                              int paise · business dates · ref tokens
                                 ▼
  L1  DETERMINISTIC MATCH     [no LLM]  conf 1.00   ~70–80% resolved
                              exact joins: order_id / settlement_id / UTR
                                 ▼
  L2  FEE MODEL INFERENCE     [no LLM]  solve r from L1's confirmed pairs
                              translator between bank (net) and ledger (gross)
                                 ▼
  L3  N:1 CANDIDATE GEN       [no LLM]  conf ≤ 0.92
                              date-window prune → subset-sum on fee-adjusted target
                              refunds enter as negative amounts, same code path
                                 ▼
  L4  LLM ADJUDICATION        [LLM]  < 10% of rows
                              many candidates → select · zero → classify
                              structured output · reason mandatory · re-verified
                                 ▼
  L5  RESOLVE & POST          ≥0.95 auto-post · 0.70–0.95 review · <0.70 exception
                              → books closed + cash position
```

Each layer consumes the **residual** of the previous one. Nothing L1 resolved is
ever reconsidered by L3 — that is what keeps the LLM budget under 10%.

### Dependency rule (§3.2)

`core/` imports nothing from this project. `matching/`, `ingest/`, `posting/`
import only `core/`. `pipeline/` wires them. `api/` and `web/` are delivery
mechanisms and contain no business logic. Enforced, not aspirational:

```bash
make layer-check      # scripts/check_layering.sh, also runs in CI
make drift-check      # the six standing checks from the Review Guide
```

---

## The rules this repo does not bend

1. **Money is `int` paise. Always.** Never float, never `Decimal` in transit.
   Rupees exist only at the display boundary.
2. **Everything is seeded.** Same seed + same scale ⇒ byte-identical dataset and
   identical metrics. The judge must be able to reproduce every number.
3. **The pipeline is idempotent.** Running twice posts nothing twice.
4. **Every automated decision carries a reason** and the evidence fields it rested on.
5. **Exact-set semantics.** Predicting 2 of 3 correct orders is *wrong*, not 67%
   right. No partial credit anywhere in scoring.
6. **Business dates in `Asia/Kolkata`**, stored as `date`. No naive datetimes, no
   `today()` in business logic.
7. **The LLM may select among candidates. It may never compute, and it is never
   trusted without verification.** `--no-llm` is a first-class supported mode.

## Synthetic data

All data is generated (`generator/`) and is visibly synthetic — no real-looking
PII, no plausible account numbers. `truth.json` is the answer key and is read
only by `eval/`, never by the agent.

---

Built to a written implementation guide and reviewed gate by gate against a
companion review guide. Both are working documents kept outside version
control; the section references throughout the code (`§4.1`, `§6.2`, ...)
point at the implementation guide.
