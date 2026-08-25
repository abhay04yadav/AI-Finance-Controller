# AI Finance Controller

**Reconciles the merchant's ledger, the gateway settlement report and the bank statement —
then closes the books and states the cash position.**

Razorpay /buildathon · Track 04 — *Run the books and the cash position*

> **A reconciliation tool matches records. A controller closes the books.**
>
> Deterministic where we can be. Probabilistic only where we must be. Measured everywhere.

---

## Table of contents

- [The problem](#the-problem)
- [What this actually builds](#what-this-actually-builds)
- [Approach](#approach)
- [Architecture](#architecture)
- [How each layer works](#how-each-layer-works)
- [Key design decisions](#key-design-decisions)
- [Measurement](#measurement)
- [Quickstart](#quickstart)
- [Project structure](#project-structure)
- [Development](#development)
- [Engineering invariants](#engineering-invariants)
- [Build status](#build-status)
- [Limitations](#limitations)
- [References](#references)

---

## The problem

Three parties hold three different records of the same money, and none of them agree.

| Party | Record | What it says |
|---|---|---|
| Merchant | `ledger.csv` | Sold ₹8,000 across three orders |
| Gateway | `settlement.csv` | Collected ₹8,000, took ₹160 MDR + ₹28.80 GST, paid out ₹7,811.20 |
| Bank | `bank.csv` | `04-Aug · CR ₹7,811.20 · "NEFT RAZORPAYSETL88 CR"` |

Proving those describe the same reality is still done by hand, in Excel, because **five
distortions stack up inside a single number**:

| # | Distortion | Why the naive matcher fails |
|---|---|---|
| 1 | **Cardinality is N:1** | One bank credit maps to many ledger rows. No join key exists. |
| 2 | **Amounts are transformed** | Bank shows *net*, ledger shows *gross*. MDR + 18% GST sit between them — and the MDR rate is merchant-specific and usually unknown. |
| 3 | **Dates are shifted** | T+2 working days, plus holiday drift, plus per-method variance. Matching on date equality returns nothing. |
| 4 | **Periods are crossed** | A refund from three weeks ago is netted out of today's credit. |
| 5 | **The bank statement is context-free** | Narration is `"NEFT RAZORPAYSETL88 CR"`. It never contains an order ID. |

Any one of these is trivial. All five in one number is why a human sits with a spreadsheet.

---

## What this actually builds

The brief asks for a closed finance-ops loop, not a matcher. That distinction drives every
decision below.

```
✗ Stops at matching:
   ingest → match → "45/50 matched ✅" → done

✓ Closes the loop:
   ingest → match → classify exception → decide action
          → post journal entry → report cash position → done
```

A controller's job is not matching. Matching is a step. Their job is *close the day's books
and tell me my cash position*, so the terminal screen is:

```
BOOKS CLOSED — 12-Aug-2026

  Auto-posted           441 entries    ₹3,24,880
  Pending review         28 entries    ₹  41,200
  Exceptions              8 items      ₹   8,400

  Revenue recognised                   ₹3,24,880
  Gateway fee expense                  ₹    6,497
  GST input credit claimable           ₹    1,169
  ─────────────────────────────────────────────────
  Cash in bank (confirmed)             ₹3,18,383
  Cash in transit                      ₹    8,400
```

*Illustrative layout — real figures come from a measured run. See [Measurement](#measurement).*

**Scope discipline.** The brief says *one* finance-ops loop. This builds reconciliation end
to end and closes it completely. It deliberately does **not** also build the cash
forecaster, the settlement Q&A agent, or the tax-line matcher. One closed loop beats four
open ones.

---

## Approach

### Deterministic first, LLM last

The instinct is to hand the batch to an LLM. This system does not, for four reasons in
order of importance:

1. **Non-determinism destroys credibility.** Anyone who reruns the demo and gets a
   different number stops trusting everything else on screen.
2. **Finance requires reproducibility.** An entry posted today must be re-derivable
   tomorrow, identically, for audit.
3. **Cost and latency.** Hundreds of LLM calls at tens of seconds, versus a handful at
   sub-second.
4. **It is mostly unnecessary.** Roughly 90% of records have exactly one arithmetically
   valid answer. There is nothing to reason about.

So the LLM is the **last mile, not the engine**, under a hard rule:

> **The LLM may select among candidates. It may never compute, and it is never trusted
> without verification.** Every verdict is re-checked in code before it can affect a
> posting.

`--no-llm` is a first-class supported mode, not a debug switch. The system runs and scores
with no LLM client installed at all — `anthropic` is an optional extra, never a base
dependency.

### Calibration is the product

A finance controller does not ask *"how accurate is your tool?"* They ask **"everything you
posted without asking me — that's all correct, right?"**

So the headline metric is not accuracy. It is **precision within the auto-post confidence
band**. Thresholds are *derived* from the measured calibration table, never guessed: if the
top bucket is not 100% precise, the threshold rises until it is.

A system that knows when it might be wrong is worth more than a system that is slightly
more accurate and cannot tell.

---

## Architecture

```
                   ledger.csv   settlement.csv   bank.csv
                        │             │             │
                        └─────────────┼─────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ L0  INGEST & NORMALIZE                                              │
│     3 heterogeneous sources → one canonical Record schema           │
│     amounts → int paise · dates → business dates · refs extracted   │
└─────────────────────────────────────────────────────────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ L1  DETERMINISTIC MATCH                       [no LLM] conf 1.00    │
│     exact joins on order_id / payment_id / settlement_id / UTR      │
│     ~70–80% resolved here, in milliseconds                          │
└─────────────────────────────────────────────────────────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ L2  FEE MODEL INFERENCE                       [no LLM]              │
│     solve r from L1's confirmed (gross, net) pairs                  │
│     → translator between bank language (net) and ledger (gross)     │
└─────────────────────────────────────────────────────────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ L3  N:1 CANDIDATE GENERATION                  [no LLM] conf ≤0.92   │
│     date-window prune → subset-sum on fee-adjusted target           │
│     refunds enter as negative amounts — same code path              │
│     emits 0, 1, or many candidates                                  │
└─────────────────────────────────────────────────────────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ L4  LLM ADJUDICATION                          [LLM] < 10% of rows   │
│     many candidates → select using narration / batch / timing       │
│     zero candidates  → classify + hypothesise + suggest action      │
│     structured output · reason mandatory · verdict re-verified      │
└─────────────────────────────────────────────────────────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ L5  RESOLVE & POST                                                  │
│     ≥0.95 → auto-post double-entry journal                          │
│     0.70–0.95 → review queue with prepared entry preview            │
│     <0.70 → exception with reason code + WHAT/WHY/ACTION            │
│     → books closed + cash position                                  │
└─────────────────────────────────────────────────────────────────────┘
```

Each layer consumes the **residual** of the previous one. Nothing L1 resolved is ever
reconsidered by L3 — that is what keeps the LLM budget under 10%.

---

## How each layer works

### L0 · Ingest & normalize

Three inconsistent CSVs become one `Record` stream, so no layer downstream ever parses a
file or thinks about column names. Amounts parse **straight to integer paise** — never via
float. Dates parse to business dates in `Asia/Kolkata`; ambiguous formats such as
`03/04/2026` raise rather than guess.

Malformed rows are **not** silently repaired. A bad row becomes an `INGEST_ERROR` exception
that surfaces on the exception page. Silent repair is how reconciliation systems lose
money.

`refs` is deliberately a *set of every identifier-shaped token found anywhere in the row*,
extracted by regex — not a single typed field. Real bank narrations bury references in
noise; a set lets the matcher attempt joins without knowing in advance which column carried
the key.

### L1 · Deterministic match

The settlement report is the **bridge document**: it is the only file carrying both
`order_id` and `utr`. So the join is two hops, not one:

```
ledger.order_id ──► settlement.order_id
                    settlement.utr ──► bank.utr
```

This is the volume layer — it resolves everything that needs no reasoning, at confidence
`1.00`, in milliseconds. A UTR appearing twice in the bank file is **not** matched; it
raises `DUPLICATE_UTR` immediately, because matching one of the pair double-posts revenue.

**L1 precision must be exactly 100%.** L1 declares certainty. If a confidence-1.00 match
can be wrong, the entire calibration argument collapses.

### L2 · Fee model inference — the system needs no configuration

The MDR is merchant-specific (1.8% / 2.0% / 2.5%, negotiated) and most merchants cannot
state it. Asking the user makes the tool useless until configured; hardcoding 2% fails on
every other merchant's data. So it is **derived from confirmed matches**:

```
net   = gross − gross·r − 0.18·gross·r
      = gross · (1 − 1.18r)

⇒  r     = (1 − net/gross) / 1.18      ← infer from known pairs
⇒  gross = net / (1 − 1.18r)           ← invert for unknown credits
```

The **median** is taken, not the mean — a single international-card row at 3.5% would drag
a mean and quietly break every downstream match. Wide dispersion implies multiple MDR
slabs, which are clustered and kept as a slab list rather than collapsed into one rate.

This is what lets the system run on **any merchant's export with zero setup**.

### L3 · N:1 candidate generation

For each unmatched bank credit, find which combination of open ledger rows explains it.

```
target  = fee_model.expected_gross(credit.amount)      # net → gross
window  = business days back from credit.value_date    # T+2 + holiday buffer
pool    = open ledger rows in window
          + open refunds in a WIDER window
solve   = subset_sum(pool.signed_amounts, target, tolerance, max_solutions=5)
```

Three points carry the design:

- **Refunds are just negative numbers.** Because `Record.signed_amount` is negative for
  outflows, a refund enters the same pool and the same solver. `2000 + 3500 + 2500 − 1200`
  falls out of the identical code path — no branch, no special case.
- **The refund window is deliberately wider than the order window.** Orders use the tight
  T+2 window; refunds use a ~45-day lookback, because a cross-period refund can be weeks
  old. That asymmetry is the entire mechanism for solving cross-period refunds.
- **Domain knowledge is a performance feature.** Subset-sum over 30 candidates is
  2³⁰ ≈ 1.07 billion combinations. The T+2 business rule prunes 30 candidates to about 5,
  which is 32 combinations — evaluated in microseconds. Brute force hangs at 500 records;
  this scales because the rules were read.

The solver is pure arithmetic in its own module — integers in, integers out, zero domain
knowledge — so it can be property-tested independently of payments. It collects up to five
solutions rather than stopping at the first: **multiple solutions is information**, and it
is precisely the signal that routes a case to adjudication.

### L4 · LLM adjudication — the last mile

Two jobs, only on what arithmetic cannot settle.

**Job A — many candidates: select.** The model receives narration, capture dates and
settlement IDs — the three signals invisible to arithmetic — and picks one, with a
mandatory reason and cited evidence fields.

**Job B — zero candidates: classify and hypothesise.** This produces the WHY and ACTION on
an exception card. Code can rank near-misses; only the model can articulate *why ₹80 is
missing* in language a controller can act on.

**Guardrails — the verdict is never trusted directly:**

| Check | Rejects |
|---|---|
| Candidate exists | The model invented an option that was never offered |
| Arithmetic holds | The model picked something that is not actually a solution |
| Reason present | A verdict with no human-readable justification |

A rejected verdict does **not** retry blindly. It falls through to an exception with reason
`ADJUDICATION_REJECTED` — itself an honest line on the exception list.

Temperature 0. Prompts are versioned and the version is logged with every verdict.
Responses are cached on a hash of the serialized input, so reruns cost nothing and stay
identical. Ambiguities are batched into one request, never one call per row.

### L5 · Resolve & post — the step that closes the loop

A matched ₹8,000 order does not post as "₹8,000 received". It decomposes:

```
Bank Account              Dr.  ₹7,811.20      ← what actually arrived
Gateway Fee (expense)     Dr.  ₹  160.00      ← MDR
GST Input Credit          Dr.  ₹   28.80      ← reclaimable tax
        To  Accounts Receivable    ₹8,000.00  ← what the customer owed
```

Debits ₹8,000.00 = Credits ₹8,000.00 — balanced, and `assert_balanced()` runs on every
entry before it can be persisted.

**That GST Input Credit line is not cosmetic.** If it is not posted separately, the merchant
silently forfeits reclaimable tax — meaningful money over a year.

**Confidence routing:**

| Band | Route | What the user sees |
|---|---|---|
| `≥ 0.95` | Auto-post | Nothing. It is in the books. |
| `0.70 – 0.95` | Review queue | Prepared entry + suggested match + reason → Approve / Reject, a two-second decision |
| `< 0.70` | Exception | WHAT / WHY / ACTION card |

**Suspense account.** Unmatched bank credits still represent real money in the bank, so
they post to `SUSPENSE`. The books therefore tie to the actual bank balance, and the
suspense balance *is* the size of the unreconciled problem — the one number a controller
reads as *"how much do I still not understand?"*

**Idempotency.** The idempotency key is a hash of the sorted ledger IDs plus the UTR and
settlement ID, with a unique index in Postgres. Reposting is a no-op, not a duplicate.

---

## Key design decisions

| Decision | Rationale |
|---|---|
| **Infer the MDR, never configure it** | Merchants cannot state their negotiated rate. Deriving it from confident matches means zero setup on any merchant's export, and it generalises to a second gateway unchanged. |
| **Money is `int` paise, always** | `0.1 + 0.2 != 0.3` silently corrupts a reconciliation. A `Money` value object makes float money *unrepresentable* rather than merely discouraged. Rupees exist only at the display boundary. |
| **Exact-set match semantics** | Predicting 2 of 3 correct orders is **wrong**, not 67% right. A half-matched settlement posts a wrong journal entry, so there is no partial credit anywhere in scoring. |
| **Generator built before the agent** | Without ground truth, "45 matched" is an unverifiable claim — perhaps 40 were right and 5 were wrong pairs. The generator runs the money flow *forward* and records the answer; the agent runs it *backward* and must rediscover it. |
| **Exceptions are the home screen** | A controller never looks at matched rows — those are done. Every minute of their day is spent on the residual. Building for the 88% that needs no attention is building for nobody. |
| **Everything is seeded and idempotent** | Same seed and scale produce a byte-identical dataset and identical metrics, so anyone can reproduce every number on their own machine. Running the pipeline twice posts nothing twice. |

### Design patterns, and where each earns its place

| Pattern | Where | Why |
|---|---|---|
| Adapter | `ingest/*_adapter.py` | Source quirks die at the boundary. A new bank's CSV format touches one file. |
| Strategy | `MatchStrategy` implementations | Matchers are interchangeable; the orchestrator never knows which is running. |
| Chain of Responsibility | `pipeline/orchestrator.py` | Each layer consumes the previous residual. Reordering is a list edit. |
| Registry + Factory | `matching/registry.py` | Enabling or disabling a layer is config, not code. |
| Null Object | `NullAdjudicator` | `--no-llm` needs zero conditionals in the orchestrator. |
| Builder | `JournalEntryBuilder` | Multi-line double-entry construction stays readable and validates on build. |
| Command | `exceptions_/actions.py` | Each exception action is an object with `execute()` / `undo()` — directly renderable as UI buttons and reversible in the audit trail. |
| Repository | `persistence/repositories.py` | Core logic never sees SQL; tests run against an in-memory repo. |
| Observer | `pipeline/audit.py` | Every decision emits an event; the audit trail is a subscriber. |
| Result / Either | `core/result.py` | Expected failures are values, not exceptions. Exceptions are reserved for bugs. |

### Extensibility

The abstraction boundaries are chosen so that three plausible next features stay cheap:

| Feature | Cost |
|---|---|
| Add chargeback reconciliation | One new `MatchStrategy`, two reason codes, one registry line. Zero existing logic modified. |
| Swap the LLM provider | One new `Adjudicator` implementation, one line in the DI container. Orchestrator, matchers and tests untouched. |
| Support a second gateway | One new `SourceAdapter`. Everything downstream already speaks `Record`, and fee inference works unchanged because it never assumed a rate. |

---

## Measurement

> Every number below is from `make eval` against planted ground truth — measured,
> never estimated, and truncated rather than rounded up (99.94% prints as 99.9%).
> Reproduce them with `make eval NO_LLM=1 SCALE=500`.
>
> **These are the deterministic core's numbers, with zero LLM calls.** The
> adjudication layer is not built yet; it can raise them but must never lower them.
>
> The harness itself is live as of gate 3 and currently scores the system at **0.0%**,
> because the agent is still a stub. That is the correct result: a harness that cannot
> report a failing score cannot be trusted to report a passing one. Percentages are
> truncated rather than rounded, so 99.94% prints as 99.9%.

### Why two accuracy numbers, not one

```
Team A: answers 95, declines 5.  94 of 95 correct.
        match rate = 95%      match precision = 98.9%   ✅

Team B: answers all 100.        82 of 100 correct.
        match rate = 100%     match precision = 82.0%   ❌
```

Team B's dashboard looks better and their books are wrecked — 18 wrong pairs posted as
journal entries, a week of cleanup for a chartered accountant. **In finance, a wrong answer
is worse than "I don't know."** Most submissions report only match rate.

### Metrics

| Metric | Definition | Why it is on the sheet | Result |
|---|---|---|---|
| **Match rate** | attempted / total | Coverage | **98.3%** (59/60) |
| **Match precision** | correct / attempted | **The real accuracy number** | **100.0%** (59/59) |
| **Exception recall** | planted caught / planted total | Proves it hunts problems, not just easy wins | 40.7% (11/27) — see below |
| **Auto-resolution** | conf ≥ 0.95 / total | The business value: how few rows a human still touches | **80.0%** (48 posted) |
| **Throughput** | records / sec | The bar asks for throughput | 507 rec/sec |
| **LLM calls & cost** | calls, cost per 100 records | The contrast against batch-into-LLM approaches | **0 calls, ₹0.00** |
| **Calibration** | precision per confidence bucket | The trust argument | see below |

**Exception recall needs its breakdown, or it reads as a 60% failure rate.** It counts
only the anomalies we *flagged*. Of the 27 planted, 11 were flagged, **15 were resolved
by matching** — a holiday-shifted settlement that got matched is handled, not missed —
and **1 was genuinely neither**. The headline metric is left exactly as §7.3 defines it
rather than redefined to look better; the split is reported beside it.

| Planted anomalies (27) | |
|---|---|
| flagged as exceptions | 11 |
| resolved by matching | 15 |
| **neither — the honest misses** | **1** (`RFND-5004`, CROSS_PERIOD_REFUND) |

### Confidence calibration

The table that justifies the auto-post threshold. Thresholds are derived from it, not
guessed — if the top bucket is not 100%, the threshold rises until it is.

| Confidence bucket | Records | Precision |
|---|---|---|
| 0.95 – 1.00 · auto-post | 48 | **100.0%** |
| 0.85 – 0.95 | 11 | 100.0% |
| 0.70 – 0.85 · human review | 0 | — |
| below 0.70 · exception | 0 | — |

48 entries posted without asking a human, and **zero of them wrong**. That is the
sentence the calibration table exists to support.

### Planned evidence

Measurement is designed to answer the obvious objections before they are raised:

- **Ablation** — `--no-llm` and `--no-fee-model` runs, to quantify what each layer
  contributes rather than asserting it.
- **Multi-seed** — mean ± standard deviation across several seeds. A single seed's number
  is an anecdote.
- **Held-out seed** — one seed never tuned against, reported separately, as the answer to
  *"did you overfit to your own data?"*
- **Self-reported misses** — any planted exception the system fails to catch is named in
  the output and in the UI, not buried in a log.

---

## Quickstart

**Requirements:** Python 3.11+. PostgreSQL is needed only from the posting layer onward. No
API key is required — the deterministic core and the `--no-llm` path run without one.

```bash
# 1. Create the project virtualenv and install everything
make setup

# 2. Generate a seeded synthetic dataset with ground truth
make generate SEED=42 SCALE=500

# 3. Score the agent against that ground truth
make eval SEED=42 SCALE=500
```

Every `make` target runs inside `.venv/` automatically — **there is no activate step**, and
nothing installs into your global Python.

<details>
<summary><b>Windows, or no GNU make</b></summary>

MinGW's `mingw32-make <target>` works, or use the bundled runner, which resolves the same
virtualenv:

```bash
python tasks.py setup
python tasks.py generate --seed 42 --scale 500
python tasks.py eval --seed 42 --scale 500 --no-llm
```

</details>

<details>
<summary><b>Working inside the virtualenv directly</b></summary>

```bash
source .venv/bin/activate         # macOS / Linux
source .venv/Scripts/activate     # Git Bash on Windows
.venv\Scripts\Activate.ps1        # PowerShell
```

</details>

Run `make help` for every target; it prints which interpreter it resolved to.

### Configuration

Copy `.env.example` to `.env` and fill in what you need.

**No secret is ever committed.** `.env` is gitignored, `.env.example` contains keys with
empty values only, and the API key is read from the environment at runtime — never from a
config file, never from source.

The LLM client is an **optional extra**, not a base dependency:

```bash
pip install -e ".[llm]"     # only needed for the adjudication layer
```

---

## Project structure

```
core/            pure domain — zero I/O, zero framework imports
  money.py         Money value object (int paise)
  dates.py         business-day calendar, DateWindow, injected Clock
  models.py        Record, Candidate, Match, JournalEntry
  reason_codes.py  the exception registry
  result.py        Result[T, E] — explicit outcomes, no bare exceptions
  config.py        every magic number, in one typed place

ingest/          L0 — one adapter per source; quirks die at the boundary
matching/        L1–L3 — exact matcher, fee model, subset matcher, pure solver
adjudication/    L4 — LLM adjudicator, null adjudicator, schemas, guardrails
posting/         L5 — chart of accounts, journal builder, router, cash position
exceptions_/     classification + Command-pattern actions
pipeline/        orchestration, audit event bus, per-stage profiling
api/             FastAPI delivery layer — no business logic
persistence/     schema + repositories (raw SQL, no ORM)
generator/       synthetic data + ground truth; one injector per failure mode
eval/            scoring harness and metric snapshots
web/             UI — exceptions, review, books, benchmark
tests/           unit · integration · golden datasets
scripts/         layering and drift enforcement
```

### The dependency rule

`core/` imports nothing from the project. `matching/`, `ingest/`, `posting/` import only
`core/`. `pipeline/` wires them. `api/` and `web/` are delivery mechanisms and contain no
business logic.

This is **enforced, not aspirational** — an AST-based check runs in CI and fails the build
on violation:

```bash
make layer-check
```

---

## Development

| Command | Purpose |
|---|---|
| `make setup` | Create `.venv/` and install with dev extras |
| `make generate` | Build a seeded synthetic dataset and its ground truth |
| `make match` | Run the reconciliation pipeline |
| `make eval` | Score against ground truth and print the report |
| `make demo` | Generate + match + eval at demo scale |
| `make test` | Run the test suite |
| `make lint` / `make typecheck` | ruff / mypy |
| `make layer-check` | Enforce the dependency rule |
| `make drift-check` | Six standing checks against slow decay |

### Quality gates

`make drift-check` runs continuously during development and fails on any of:

1. A float anywhere in the money path
2. A wall-clock call inside business logic
3. A swallowed exception (`except: pass`)
4. A layering violation
5. An LLM client referenced outside the adjudication layer before it is due
6. Order IDs leaking into the generated bank narration

Checks 1–3 and 5 are tokenizer-based and read **code only**, so a rule written in a
docstring is never mistaken for a violation of itself. Each check is verified by planting a
deliberate violation and confirming it is caught.

### Testing strategy

| Level | What |
|---|---|
| **Unit** | `Money`, business calendar, fee model; the subset solver via property tests |
| **Integration** | Full pipeline on a small golden dataset with frozen expected metrics |
| **Contract** | LLM response schema against recorded fixtures, so it runs offline |
| **Regression** | CI runs the eval; a precision drop fails the build |

---

## Engineering invariants

Enforced by tests and CI, not by convention:

1. **Money is `int` paise. Always.** Never float, never `Decimal` in transit. Rupees exist
   only at the display boundary.
2. **Everything is seeded.** Same seed and scale produce a byte-identical dataset and
   identical metrics.
3. **The pipeline is idempotent.** Running twice posts nothing twice, guarded by a unique
   idempotency key per journal entry.
4. **Every automated decision carries a reason** and the evidence fields it rested on. No
   entry posts without a human-readable justification.
5. **Exact-set semantics for matches.** No partial credit anywhere in scoring.
6. **Dates are business dates in `Asia/Kolkata`**, stored as `date`. No naive datetimes, no
   UTC drift, no wall-clock reads in business logic — the clock is injected.
7. **Double entry balances.** `assert_balanced()` on every entry, a run-level assertion,
   and the tie-out `bank_account + suspense == sum(bank credits)`.
8. **Every decision is audited.** Who decided this, on what evidence, when, and under which
   prompt version.

### Synthetic data

All data is generated and is **visibly synthetic** — no real-looking personal data, no
plausible account numbers, no real merchant information. Ground truth is read only by the
scoring harness and never by the agent.

Critically, the generated bank narration **never contains order IDs**. Leaking them would
make the problem trivially solvable and every reported accuracy number meaningless, so it
is asserted continuously as a standing check.

---

## Build status

The build order is deliberate: ground truth and scoring come before the agent, and the LLM
arrives at step 11 of 14. Building the LLM early means leaning on it and leaving the
deterministic core weak. Building it last yields a system that works without AI — more
robust, and a far better story.

| # | Gate | Verification | Status |
|---|---|---|---|
| 0 | Repo skeleton | Structure and layering enforced | ✅ |
| 1 | Core domain | Float money impossible to construct | ✅ |
| 2 | Generator | Deterministic; zero order IDs in bank narration | ✅ |
| 3 | Eval harness | Exact-set-equality scoring | ✅ |
| 4 | L0 ingest | Totals tie; idempotent | ✅ |
| 5 | L1 exact matcher | **Precision exactly 100%** | ✅ |
| 6 | L2 fee model | Recovers planted rate; median not mean | ✅ |
| 7 | L3 subset matcher | Property tests; bounded pool and latency | ✅ |
| 8 | L5 posting | Entries balance; books tie; idempotent | ✅ |
| 9 | Exceptions + actions | WHAT / WHY / ACTION on every card | ✅ |
| 10 | **No-LLM checkpoint** | Deterministic core stands alone, zero LLM calls | ✅ |
| 11 | L4 adjudication | Bounded budget; deterministic; guardrails pass | ⬜ |
| 12 | UI | Exceptions is home; entry preview in review | ⬜ |
| 13 | Benchmark screen | Runs live; surfaces its own misses | ⬜ |
| 14 | Final | Clean clone; held-out seed holds | ⬜ |

---

## Limitations

Stated deliberately. A known limitation is a design boundary; an unstated one is a surprise.

**Scope**

- **One loop, not four.** Reconciliation is closed end to end. The cash forecaster,
  settlement Q&A agent and tax-line matcher named in the brief are explicitly out of scope.
  The cash position reported here is the *output* of the reconciliation loop, not a
  separate forecasting feature.
- **One gateway's report shape.** A second gateway needs a new adapter. The design keeps
  that to a single file, but it is not written.
- **INR only.** No multi-currency ledger, no FX revaluation. International cards are
  handled as an MDR-slab variance, not as a currency conversion.

**Data**

- **Synthetic data only.** No real merchant export exists for this, and none would carry
  the labels needed to measure accuracy. Realism is bounded by how faithfully the generator
  models documented gateway and regulator behaviour — every planted failure mode traces to
  a cited source, but real data will contain modes not modelled here.
- **Ground truth is planted, not discovered.** Measured accuracy is accuracy *against the
  generator's model of reality*. That is the honest framing of every number reported.

**Method**

- **The fee model assumes a linear MDR plus GST.** Fixed per-transaction fees, tiered
  volume pricing, and negotiated per-method rates beyond slab clustering are not modelled.
- **The subset solver is bounded by design.** Beyond a pool-size ceiling it returns no
  candidates and the credit becomes an exception rather than hanging the run. Correct
  behaviour, but it means very wide candidate pools are declined rather than solved.
- **Rounding drift is a distinct, lower-confidence class,** not something absorbed by
  loosening the exact matcher. Intentional — it keeps the failure visible in the metrics
  rather than hidden inside a tolerance.
- **The LLM is a selector, never a calculator.** It cannot rescue a case the deterministic
  layers failed to generate candidates for; it can only classify and hypothesise about it.

**Operational**

- **Durable idempotency is enforced by the schema, but exercised only
  in-memory.** The default repository is in-process, so a clean clone
  reconciles 5,000 records with no database to install. That means the
  "running twice posts nothing twice" guarantee is verified *within* a process:
  the second `post()` of an entry is refused, all 64 of them. It is **not**
  verified across two separate invocations, because a fresh process starts with
  an empty book. The real cross-process guarantee is the `UNIQUE` index on
  `journal_entries.idempotency_key` in `persistence/schema.sql`, which is
  written and matches the in-memory behaviour — but no Postgres-backed test
  runs in CI yet.

- **Not multi-tenant.** Single merchant, single run context. No authentication or
  authorisation layer.
- **Resumability is per-run.** Partially completed runs do not leave half-posted books, but
  distributed or concurrent execution is out of scope.

---

## References

Every gateway and regulator behaviour modelled by the generator is documented, not
invented:

- **Payment gateway flow, Orders API, signature verification** — Razorpay Docs,
  *Payment Gateway — How it works* / *Standard Checkout Integration*
- **Authorization → capture states, three-day capture-or-auto-refund** — Razorpay Docs,
  *Payment Capture Settings*
- **Late authorization and three-day bank polling** — Razorpay Docs,
  *Late Payment Authorisations*
- **T+2 settlement cycle, working-day definition, UTR per settlement, settlement
  reconciliation report** — Razorpay Docs, *Settlements* / *Settlements FAQs*
- **Partial settlement mechanics — whole transactions, never split amounts** —
  Razorpay Docs, *About Settlements*
- **Escrow account, Tp+0/Tp+1 remittance, permitted credits and debits, no float
  interest** — RBI, *Guidelines on Regulation of Payment Aggregators and Payment Gateways*

### Design documents

The full specification this was built to, and the gate-by-gate review guide it
was checked against, are in [`docs/`](docs/):

- [`docs/AI Finance Controller.md`](docs/AI%20Finance%20Controller.md) — implementation guide.
  The `§` references throughout the code (`§4.1`, `§6.2`, …) point here.
- [`docs/AI Finance Controller — Review Guide.md`](docs/AI%20Finance%20Controller%20%E2%80%94%20Review%20Guide.md)
  — the 14 verification gates, each with the number it has to hit.

---

<div align="center">

**One loop, closed completely, measured honestly.**

</div>
