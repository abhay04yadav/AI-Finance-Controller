"""Failure-mode injectors. One module per exception type. Guide §6.2 step 3.

Every injector conforms to one protocol and follows one shape:
**mutate the world → record the truth.**

Every injector must model a behaviour documented in Razorpay's or the RBI's own
material (Appendix C). An injector that cannot be traced to a real behaviour is
an invented failure mode, and it tests nothing.

Counts are **ratios of scale**, never fixed numbers, so behaviour is comparable
at 50 records and at 5,000. A floor of one instance applies so that every
failure mode is still exercised at the 50-record bar the brief sets; above ~250
records the ratios dominate entirely.
"""

from __future__ import annotations

from random import Random
from typing import Protocol

from core.reason_codes import ReasonCode
from generator.world import TruthException, World


class Injector(Protocol):
    """Plant `count` instances of one failure mode, returning what was planted."""

    reason_code: ReasonCode
    #: The thing this anomaly attaches to: an order, or a whole settlement.
    #: §6.2 states ratios as a share of "records", which is the right denominator
    #: for a ledger-row anomaly but not for a bank-row one — a duplicated UTR is
    #: a property of a settlement, not of the 42 orders inside it. Applying an
    #: order ratio to a batch-level mode overflows the batch pool the moment
    #: orders-per-batch grows, which silently starves the later injectors.
    unit: str
    #: Share of that unit this mode accounts for.
    ratio: float
    #: The real-world behaviour modelled, and where it is documented.
    models: str

    def inject(self, world: World, rng: Random, count: int) -> list[TruthException]: ...


def count_for(ratio: float, units: int) -> int:
    """How many instances to plant at this scale.

    Ratio-driven with a floor of one: the Review Guide requires all eight modes
    to appear even at `--scale 50`, where 0.4% would otherwise round to zero.
    `units` is the count of whatever the injector attaches to — orders or batches.
    """
    return max(1, round(ratio * units))


def build_registry() -> list[Injector]:
    """Every injector, in a fixed order.

    The order is deliberate and must not be sorted or shuffled: injectors mutate
    shared state, so the sequence is part of what makes a seed reproducible.
    Order removal runs before refunds and fee perturbation so that a deferred
    order never has drift planted on a batch it has already left.
    """
    from generator.injectors.auto_refund import AutoRefundedInjector
    from generator.injectors.cross_period_refund import CrossPeriodRefundInjector
    from generator.injectors.duplicate_utr import DuplicateUtrInjector
    from generator.injectors.holiday_shift import HolidayShiftInjector
    from generator.injectors.late_authorization import LateAuthorizationInjector
    from generator.injectors.missing_in_ledger import MissingInLedgerInjector
    from generator.injectors.partial_settlement import PartialSettlementInjector
    from generator.injectors.rounding_drift import RoundingDriftInjector

    return [
        PartialSettlementInjector(),
        AutoRefundedInjector(),
        LateAuthorizationInjector(),
        CrossPeriodRefundInjector(),
        HolidayShiftInjector(),
        RoundingDriftInjector(),
        MissingInLedgerInjector(),
        DuplicateUtrInjector(),
    ]
