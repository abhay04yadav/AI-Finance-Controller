"""Synthetic dataset generator. Guide §6. The exam paper AND the answer key.

    python -m generator.generate --seed 42 --scale 50      # clears the 50-record bar
    python -m generator.generate --seed 42 --scale 5000    # the demo run
    python -m generator.generate --seed 7  --scale 500     # held-out seed

Runs the real money flow (§1.3) FORWARD and records the answer. The agent runs it
BACKWARD and must rediscover it.

Determinism (§2.7 rule 2) is a checksum claim, so it is engineered rather than
hoped for: one seeded Random instance, every collection sorted before iteration,
no wall-clock read anywhere, and explicit "\\n" line endings on every file.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
from random import Random

from core.config import Settings
from core.console import configure_stdout
from core.dates import BusinessCalendar, DateWindow
from core.reason_codes import ReasonCode
from generator.injectors import build_registry, count_for
from generator.validate import validate
from generator.world import Batch, Order, World
from generator.writers import (
    GENERATOR_VERSION,
    write_bank,
    write_ledger,
    write_settlement,
    write_truth,
)

#: Fixed anchor so a seed pins the calendar too. A Monday, deliberately.
PERIOD_START = date(2026, 1, 5)

#: Roughly this many captures per working day. Keeps batches the size a real
#: mid-market merchant sees, and keeps the L3 candidate pool inside the window
#: small enough for subset-sum to stay cheap (§2.4).
ORDERS_PER_DAY = 8

MIN_PERIOD_DAYS = 10
MAX_PERIOD_DAYS = 120

#: The rate the merchant negotiated. Never told to the agent, which must infer
#: it from confident matches (§2.3).
#:
#: Deliberately NOT 2.00%, for two reasons that point the same way:
#:
#:   1. 2.00% is the value L2 falls back to when it has too few samples (§4.2
#:      step 5). A dataset at exactly that rate cannot prove inference works —
#:      an L2 that never ran would return 0.02 and pass a "within 0.1%" check.
#:   2. A round number is not evidence. "Inferred MDR: 2.0000%" is
#:      indistinguishable from a hardcoded constant to anyone watching the demo.
#:      1.83% is a rate nobody would hardcode, which is the entire point of
#:      §4.2's "we were never told the MDR".
#:
#: 1.83% is a plausible negotiated domestic card rate, and it is far enough from
#: every entry in PLAUSIBLE_SLABS that the display snap never fires on it.
PLANTED_FEE_RATE = 0.0183

#: A different MDR slab, for international cards (§1.5 FX_OR_SLAB_VARIANCE).
INTL_FEE_RATE = 0.035

#: How much of the period the merchant's settlement report actually covers.
#:
#: Reports are fetched for a bounded window, so the oldest credits in a bank
#: statement routinely have no report rows behind them. Those are the genuine
#: N:1 cases of §1.4 — a credit, a pile of orders, and no join key — and they
#: are what L3's date-window prune and subset-sum exist to solve (§4.3).
#:
#: At 1.0 the bridge resolves essentially every credit, L1 covers ~92% and L3
#: inherits almost nothing. §3.1 budgets L1 at ~70-80%, and the gate 5 band is
#: 60-90%, so the default leaves a real residual behind.
REPORT_COVERAGE = 0.85

MIN_ORDER_PAISE = 50_000  # ₹500
MAX_ORDER_PAISE = 500_000  # ₹5,000


def build_period(calendar: BusinessCalendar, scale: int) -> tuple[DateWindow, list[date]]:
    """The business days orders are captured on."""
    wanted = min(MAX_PERIOD_DAYS, max(MIN_PERIOD_DAYS, scale // ORDERS_PER_DAY))
    days: list[date] = []
    cursor = PERIOD_START
    while len(days) < wanted:
        if calendar.is_business_day(cursor):
            days.append(cursor)
        cursor += timedelta(days=1)
    return DateWindow(days[0], days[-1]), days


def build_world(
    seed: int,
    scale: int,
    settings: Settings,
    fee_rate: float = PLANTED_FEE_RATE,
) -> World:
    """Steps 1 and 2 of §6.2: create the truth, then replay the flow forward."""
    rng = Random(seed)
    calendar = BusinessCalendar()
    period, business_days = build_period(calendar, scale)

    world = World(
        seed=seed,
        scale=scale,
        fee_rate=fee_rate,
        gst_rate=settings.gst_rate,
        calendar=calendar,
        period=period,
    )

    # -- Step 1: orders -----------------------------------------------------
    for i in range(scale):
        capture_date = business_days[i % len(business_days)]
        order = Order(
            order_id=f"ORD-{1000 + i}",
            # Multiples of ₹1 keep amounts realistic and collisions plausible,
            # which is what makes N:1 genuinely ambiguous rather than trivially
            # unique.
            amount_paise=rng.randrange(MIN_ORDER_PAISE, MAX_ORDER_PAISE, 100),
            capture_date=capture_date,
        )
        world.orders[order.order_id] = order

    # -- Step 2: replay the money flow forward ------------------------------
    by_capture: dict[date, list[str]] = {}
    for oid in sorted(world.orders):
        by_capture.setdefault(world.orders[oid].capture_date, []).append(oid)

    for n, capture_date in enumerate(sorted(by_capture), start=1):
        # T begins at CAPTURE, not order creation (§1.3 phase 3).
        settle_date = calendar.add_business_days(capture_date, settings.settlement_days)
        world.batches.append(
            Batch(
                settlement_id=f"SETL-{100 + n}",
                utr=f"UTR-{rng.randrange(10_000_000, 99_999_999)}",
                settle_date=settle_date,
                order_ids=sorted(by_capture[capture_date]),
            )
        )
    return world


def inject_failures(world: World, seed: int) -> dict[str, int]:
    """Step 3 of §6.2: plant every failure mode, labelling as it goes."""
    planted: dict[str, int] = {}
    for injector in build_registry():
        # A per-injector stream, so adding or reordering one injector does not
        # shift the random draws of the others.
        rng = Random(f"{seed}:{injector.reason_code}")
        units = len(world.orders) if injector.unit == "order" else len(world.batches)
        wanted = count_for(injector.ratio, units)
        got = injector.inject(world, rng, wanted)
        planted[str(injector.reason_code)] = len(got)
    return planted


def apply_report_coverage(world: World, coverage: float) -> int:
    """Withhold the oldest settlements from the report the merchant holds.

    Oldest rather than random, because that is how it actually happens: the
    bank statement spans the whole period, the settlement report was pulled for
    a recent window, and everything before it has to be reconciled without a
    bridge.

    Batches whose ledger rows were withheld (MISSING_IN_LEDGER) stay in the
    report — that exception is *about* the settlement report knowing something
    the books do not, so removing its rows would erase it.
    """
    if coverage >= 1.0:
        return 0
    eligible = sorted(
        (b for b in world.batches if not b.orders_hidden_from_ledger),
        key=lambda b: (b.settle_date, b.settlement_id),
    )
    withheld = len(eligible) - round(coverage * len(eligible))
    for batch in eligible[:max(0, withheld)]:
        batch.in_report = False
    return max(0, withheld)


def apply_slab_variance(world: World, seed: int, ratio: float, intl_rate: float) -> int:
    """Give some settlements a different MDR slab (§4.2 step 3, §1.5).

    Opt-in rather than part of the standard dataset, so the default stays
    single-slab and gate 5's expectations do not move. Its purpose is to prove
    L2 takes a MEDIAN: a handful of international rows at 3.5% must not drag the
    inferred domestic rate. A mean would be dragged, and would then mis-price
    every credit downstream.
    """
    if ratio <= 0:
        return 0
    rng = Random(f"{seed}:slab")
    pool = [b for b in world.batches if b.fee_rate_override is None and b.order_ids]
    count = max(1, round(ratio * len(pool)))
    for batch in rng.sample(pool, min(count, len(pool))):
        batch.fee_rate_override = intl_rate
        world.record(batch.utr, ReasonCode.FX_OR_SLAB_VARIANCE)
    return count


def generate(
    seed: int,
    scale: int,
    out_dir: Path,
    settings: Settings | None = None,
    *,
    fee_rate: float = PLANTED_FEE_RATE,
    intl_ratio: float = 0.0,
    intl_rate: float = INTL_FEE_RATE,
    report_coverage: float = REPORT_COVERAGE,
) -> dict:
    """Generate a complete dataset and validate it before returning."""
    settings = settings or Settings()
    out_dir.mkdir(parents=True, exist_ok=True)

    world = build_world(seed, scale, settings, fee_rate=fee_rate)
    planted = inject_failures(world, seed)
    apply_report_coverage(world, report_coverage)
    slabbed = apply_slab_variance(world, seed, intl_ratio, intl_rate)
    if slabbed:
        planted[str(ReasonCode.FX_OR_SLAB_VARIANCE)] = slabbed

    # -- Steps 4 and 5: write the files, then the answer key ---------------
    write_ledger(out_dir / "ledger.csv", world)
    write_settlement(out_dir / "settlement.csv", world)
    write_bank(out_dir / "bank.csv", world)
    truth = write_truth(out_dir / "truth.json", world)

    stats = validate(out_dir)
    stats["planted"] = planted  # type: ignore[assignment]
    return {"truth": truth, "stats": stats, "world": world}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generator.generate",
        description="Generate a seeded synthetic reconciliation dataset with ground truth.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scale", type=int, default=500, help="number of orders")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--fee-rate",
        type=float,
        default=PLANTED_FEE_RATE,
        help=(
            "MDR to plant. The default is deliberately non-round: 2.00% is "
            "L2's own fallback, so a dataset at that rate could not tell a "
            "working fee model from one that never ran."
        ),
    )
    parser.add_argument(
        "--intl-ratio",
        type=float,
        default=0.0,
        help="share of settlements on a different MDR slab (proves the median holds)",
    )
    parser.add_argument("--intl-rate", type=float, default=INTL_FEE_RATE)
    parser.add_argument(
        "--report-coverage",
        type=float,
        default=REPORT_COVERAGE,
        help=(
            "share of settlements the merchant's report covers. 1.0 gives L1 a "
            "bridge for every credit and leaves L3 nothing to do."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    out_dir = args.out or Path("data") / f"seed{args.seed}"
    result = generate(
        args.seed,
        args.scale,
        out_dir,
        fee_rate=args.fee_rate,
        intl_ratio=args.intl_ratio,
        intl_rate=args.intl_rate,
        report_coverage=args.report_coverage,
    )

    if args.quiet:
        return 0

    configure_stdout()

    s = result["stats"]
    print(f"dataset written to {out_dir}  (generator v{GENERATOR_VERSION})")
    print(f"  seed {args.seed} | scale {args.scale} | MDR {args.fee_rate:.4%}")
    print(
        f"  ledger {s['ledger_rows']} rows · settlement {s['settlement_rows']} · "
        f"bank {s['bank_rows']}"
    )
    print(f"  mappings {s['mappings']} · planted exceptions {s['exceptions']}")
    for code, n in sorted(s["planted"].items()):
        print(f"      {code:<24} {n}")
    print("  self-validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
