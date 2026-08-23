"""The mutable world the generator builds and injectors perturb. Guide §6.2.

The generator runs the real money flow (§1.3) **forward** and records the answer.
The agent runs it **backward** and must rediscover it. This module is the state
that flow operates on.

Every injector follows one shape: **mutate the world → record the truth.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from core.dates import BusinessCalendar, DateWindow
from core.reason_codes import ReasonCode


class OrderStatus:
    """Ledger row status, mirroring the payment states in §1.3.

    CAPTURED    money captured; the settlement clock (T) starts here
    AUTHORIZED  authorized but never captured — auto-refunded after 3 days
    FAILED      bank returned failure; may still resurrect (late authorization)
    REFUND      a refund line, carried as a negative amount
    """

    CAPTURED = "captured"
    AUTHORIZED = "authorized"
    FAILED = "failed"
    REFUND = "refund"


@dataclass
class Order:
    """One sale in the merchant's ledger."""

    order_id: str
    amount_paise: int
    capture_date: date
    status: str = OrderStatus.CAPTURED


@dataclass
class Refund:
    """A refund line. Carried positive here; written negative to the ledger and
    deducted from a batch's net (§1.4 reason 4)."""

    refund_id: str
    amount_paise: int
    refund_date: date
    original_order_id: str


@dataclass
class Batch:
    """One settlement: a set of WHOLE transactions paid out under one UTR.

    Totals are derived, never stored, so that any injector mutating the member
    lists cannot leave a stale total behind — the class of generator bug that
    looks exactly like a matcher bug (§6.3).
    """

    settlement_id: str
    utr: str
    settle_date: date
    order_ids: list[str] = field(default_factory=list)
    #: Cross-period refunds netted out of this batch but NOT itemised in the
    #: settlement report — which is what makes the batch total unexplainable
    #: without a wider refund search (§4.3b).
    refund_ids: list[str] = field(default_factory=list)
    #: ROUNDING_DRIFT: ±1–50 paise of gateway fee rounding.
    fee_adjustment_paise: int = 0
    #: True when T+2 landed on a non-working day and settlement slipped.
    holiday_shifted: bool = False
    #: MISSING_IN_LEDGER: money arrived, the merchant never recorded the sale.
    orders_hidden_from_ledger: bool = False

    def gross(self, world: World) -> int:
        """What the customers were charged. Fees are computed on this."""
        return sum(world.orders[oid].amount_paise for oid in self.order_ids)

    def fee(self, world: World) -> int:
        """MDR, plus any planted rounding drift."""
        return int(self.gross(world) * world.fee_rate) + self.fee_adjustment_paise

    def gst(self, world: World) -> int:
        """18% GST on the fee — reclaimable by the merchant (§4.5)."""
        return int(self.fee(world) * world.gst_rate)

    def refund_total(self, world: World) -> int:
        return sum(world.refunds[rid].amount_paise for rid in self.refund_ids)

    def net(self, world: World) -> int:
        """What actually lands in the bank.

        Matches the §1.4 worked example exactly:
            8000 gross − 160 fee − 28.80 GST − 1200 refund = 6611.20
        """
        return (
            self.gross(world)
            - self.fee(world)
            - self.gst(world)
            - self.refund_total(world)
        )

    def members(self) -> list[str]:
        """Every ledger row this credit is composed of — the answer key entry."""
        return sorted(self.order_ids) + sorted(self.refund_ids)


@dataclass(frozen=True)
class TruthException:
    """One planted anomaly, labelled at the moment it is created.

    `ref` is whichever identifier the anomaly attaches to — an order for
    ledger-side problems, a UTR for bank-side ones.
    """

    ref: str
    type: ReasonCode


@dataclass
class World:
    """Everything the generator knows. Injectors receive this and mutate it."""

    seed: int
    scale: int
    fee_rate: float
    gst_rate: float
    calendar: BusinessCalendar
    period: DateWindow
    orders: dict[str, Order] = field(default_factory=dict)
    refunds: dict[str, Refund] = field(default_factory=dict)
    batches: list[Batch] = field(default_factory=list)
    exceptions: list[TruthException] = field(default_factory=list)
    #: Bank rows to emit twice (DUPLICATE_UTR), applied at write time.
    duplicated_utrs: set[str] = field(default_factory=set)
    #: Refs already perturbed, so two injectors never fight over one record.
    claimed: set[str] = field(default_factory=set)
    _next_refund: int = 0

    # ------------------------------------------------------------- selection

    def unclaimed_batches(self, *, min_orders: int = 1) -> list[Batch]:
        """Batches no injector has touched yet, in deterministic order."""
        return [
            b
            for b in self.batches
            if b.settlement_id not in self.claimed
            and len(b.order_ids) >= min_orders
            and not b.orders_hidden_from_ledger
        ]

    def unclaimed_orders(self, *, removable: bool = False) -> list[tuple[Batch, str]]:
        """(batch, order) pairs no injector has touched, in deterministic order.

        `removable=True` additionally requires the batch to keep at least two
        orders after one is taken, so detaching can never empty a settlement or
        collapse it to a trivial 1:1 match.
        """
        out: list[tuple[Batch, str]] = []
        for b in self.batches:
            if b.orders_hidden_from_ledger or b.settlement_id in self.claimed:
                continue
            if removable and len(b.order_ids) <= 2:
                continue
            out.extend((b, oid) for oid in sorted(b.order_ids) if oid not in self.claimed)
        return out

    def claim(self, ref: str) -> None:
        self.claimed.add(ref)

    def record(self, ref: str, reason: ReasonCode) -> TruthException:
        """Label an anomaly and claim its ref in one step."""
        exc = TruthException(ref=ref, type=reason)
        self.exceptions.append(exc)
        self.claim(ref)
        return exc

    # -------------------------------------------------------------- mutation

    def next_refund_id(self) -> str:
        self._next_refund += 1
        return f"RFND-{5000 + self._next_refund}"

    def detach_order(self, batch: Batch, order_id: str) -> None:
        """Remove one WHOLE order from a settlement.

        Never splits an amount. Partial settlement defers complete transactions
        and pushes them to a later slot (§1.3 phase 5); splitting would make N:1
        matching impossible by construction.
        """
        batch.order_ids.remove(order_id)

    # --------------------------------------------------------------- queries

    def settled_order_ids(self) -> set[str]:
        return {oid for b in self.batches for oid in b.order_ids}

    def mappings(self) -> dict[str, list[str]]:
        """THE ANSWER KEY: bank UTR → the ledger rows that credit is made of.

        Orphan batches (MISSING_IN_LEDGER) are excluded: there is nothing in the
        ledger for them to map to, which is precisely the finding.
        """
        return {
            b.utr: b.members()
            for b in self.batches
            if not b.orders_hidden_from_ledger and b.members()
        }
