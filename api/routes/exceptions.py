"""The exception list, as the hero screen consumes it. Guide §5.7, §8.

`/api/runs/{id}/exceptions` returns WHAT / WHY / ACTION per card, sorted by
amount descending — a controller triages by size, so the biggest problem is
first (§8.4).

In-transit money is returned in its own collection, never mixed into the
exception list. Appendix A is explicit: it is not a failure, and a screen that
shows it as one is factually wrong.

This layer contains no business logic (§3.2). It serialises a `RunResult` and
nothing else.
"""

from __future__ import annotations

from typing import Any

from core.money import Money
from core.run_result import ExceptionOutcome, RunResult


def serialize_exception(exc: ExceptionOutcome) -> dict[str, Any]:
    """One card. The UI renders `actions` as buttons without knowing what they
    mean — adding an action changes this payload, not the frontend (§8.3)."""
    return {
        "ref": exc.ref,
        "reason_code": str(exc.reason_code),
        "severity": str(exc.severity),
        "amount_paise": exc.amount_paise,
        "amount": str(Money(exc.amount_paise)) if exc.amount_paise else None,
        "value_date": exc.value_date.isoformat() if exc.value_date else None,
        "what": exc.what,
        "why": exc.why,
        "actions": [
            {
                "code": a.code,
                "label": a.label,
                "description": a.description,
                "posts_entry": a.posts_entry,
            }
            for a in exc.actions
        ],
    }


def exceptions_payload(result: RunResult) -> dict[str, Any]:
    """Everything the exception screen needs, in one response."""
    problems = [e for e in result.exceptions if not e.is_in_transit]
    in_transit = [e for e in result.exceptions if e.is_in_transit]
    problems.sort(key=lambda e: -(e.amount_paise or 0))
    in_transit.sort(key=lambda e: -(e.amount_paise or 0))

    unreconciled = sum(e.amount_paise or 0 for e in problems)
    return {
        # The header line: "8 open · ₹8,400 unreconciled" (§8.4).
        "open": len(problems),
        "unreconciled_paise": unreconciled,
        "unreconciled": str(Money(unreconciled)),
        "exceptions": [serialize_exception(e) for e in problems],
        "in_transit": {
            "count": len(in_transit),
            "total_paise": result.cash_position.in_transit,
            "total": str(Money(result.cash_position.in_transit)),
            "items": [serialize_exception(e) for e in in_transit],
        },
    }
