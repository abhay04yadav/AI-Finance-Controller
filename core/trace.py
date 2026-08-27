"""The reconciliation trace, as a value. Guide §8.5 — the signature element.

    ORD-3312 ──▶ SETL-91? ──▶ expected settlement ──▶ CREDIT LANDED
                             − fee @ 1.83%
                             − GST on fee
                             − ₹1,067.76 UNEXPLAINED

The TYPES live here and the BUILDERS live in `pipeline/trace.py`, the same split
`CashPosition` already uses: `core/` may import nothing from the project, and a
trace has to be assembleable from records, a fee model and a match — none of
which `core/` is allowed to see.

The frontend receives nodes, steps and a residual, and decides where to put them
on an SVG canvas. It knows nothing about fees, MDR slabs or settlement windows.
That split is the point: a trace assembled in a React component is a picture of
what someone believed the pipeline did, and it goes stale the first time the
pipeline changes its mind.

`steps` carries signed paise so the UI prints the sign it is given rather than
deciding which operations subtract. The arithmetic stays on this side, where it
is tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class TraceNode:
    """One source row on the left of the trail."""

    id: str
    amount_paise: int
    value_date: date | None
    kind: str  # "order" | "refund"
    settlement_id: str | None = None
    #: Set when this row was considered and passed over, with the reason.
    rejected: bool = False
    rejected_because: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "amount_paise": self.amount_paise,
            "value_date": self.value_date.isoformat() if self.value_date else None,
            "kind": self.kind,
            "settlement_id": self.settlement_id,
            "rejected": self.rejected,
            "rejected_because": self.rejected_because,
        }


@dataclass(frozen=True, slots=True)
class TraceStep:
    """One line of the arithmetic in the middle of the trail.

    `signed_paise` is negative for a subtraction, so the UI prints the sign it
    is given rather than deciding which operations subtract — the arithmetic
    stays here, where it can be tested.
    """

    label: str
    signed_paise: int
    note: str = ""
    kind: str = "adjust"  # "open" | "adjust" | "subtotal" | "residual"

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "signed_paise": self.signed_paise,
            "note": self.note,
            "kind": self.kind,
        }


@dataclass(frozen=True, slots=True)
class Trace:
    """One complete trail, ready to draw."""

    ref: str
    outcome: str  # "explained" | "unexplained"
    nodes: tuple[TraceNode, ...] = ()
    steps: tuple[TraceStep, ...] = ()
    settlement_id: str | None = None
    settlement_known: bool = True
    credit_paise: int = 0
    credit_value_date: date | None = None
    #: What the arithmetic could not account for. Zero on an explained trace.
    residual_paise: int = 0
    fee_rate: float | None = None
    gst_rate: float | None = None
    #: Rows still open in the window, and what they add up to. The number that
    #: separates "₹80 short of a near miss" from "nothing here comes close".
    open_pool_rows: int = 0
    open_pool_paise: int = 0
    candidates: tuple[tuple[str, ...], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "outcome": self.outcome,
            "nodes": [n.as_dict() for n in self.nodes],
            "steps": [s.as_dict() for s in self.steps],
            "settlement_id": self.settlement_id,
            "settlement_known": self.settlement_known,
            "credit_paise": self.credit_paise,
            "credit_value_date": (
                self.credit_value_date.isoformat() if self.credit_value_date else None
            ),
            "residual_paise": self.residual_paise,
            "fee_rate": self.fee_rate,
            "gst_rate": self.gst_rate,
            "open_pool_rows": self.open_pool_rows,
            "open_pool_paise": self.open_pool_paise,
            "candidates": [list(c) for c in self.candidates],
        }
