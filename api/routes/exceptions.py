"""The exception list, as the hero screen consumes it. Guide §5.7, §8.

`/api/runs/{id}/exceptions` returns WHAT / WHY / ACTION per card, sorted by
amount descending — a controller triages by size, so the biggest problem is
first (§8.4).

In-transit money is returned in its own collection, never mixed into the
exception list, never in the header count, and never blocking a close.
Appendix A is explicit: it is not a failure, and a screen that shows it as one
is factually wrong.

**The open balance is computed here, not in the browser.** Frame 2a's third
column is a running balance descending to zero — row N carries what is still
open from row N onwards — and the nine amounts must actually sum to the header
figure. Computing it server-side means one place owns the arithmetic and the
`ties` flag beside it is a real check rather than a restatement.

**WHY is passed through untouched.** No fallback text, no templating. If §4.4's
job B is wired, that string is the model's hypothesis; if it is not, it is the
deterministic classifier's sentence. `why_source` says which, so the card can
mark a hypothesis as a hypothesis — and so nobody has to guess later.

This layer contains no business logic (§3.2). It serialises a `RunResult`, the
book, and the audit trail, and nothing else.
"""

from __future__ import annotations

from typing import Any

from core.money import Money
from core.run_result import ExceptionOutcome, RunResult

#: Text L4's job B stamps onto a `why` it authored (see
#: `pipeline.adjudication_step.apply_hypotheses`). Its presence is how a card
#: knows to label the sentence as a hypothesis rather than a finding.
_MODEL_MARKER = "Adjudicator ("


def why_source(exc: ExceptionOutcome) -> str:
    """"model" when L4 wrote this sentence, "classifier" when the rules did.

    §8.2 says WHY comes from job B *or* from the deterministic classifier. Both
    are legitimate; conflating them is not. A controller reading a hypothesis
    should know it is one.
    """
    return "model" if _MODEL_MARKER in (exc.why or "") else "classifier"


def serialize_exception(
    exc: ExceptionOutcome,
    *,
    open_balance_paise: int | None = None,
    trace: Any = None,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One card. The UI renders `actions` as buttons without knowing what they
    mean — adding an action changes this payload, not the frontend (§8.3)."""
    return {
        "ref": exc.ref,
        "reason_code": str(exc.reason_code),
        "severity": str(exc.severity),
        "in_transit": exc.is_in_transit,
        "amount_paise": exc.amount_paise,
        "amount": str(Money(exc.amount_paise)) if exc.amount_paise else None,
        "value_date": exc.value_date.isoformat() if exc.value_date else None,
        "what": exc.what,
        "why": exc.why,
        "why_source": why_source(exc),
        "open_balance_paise": open_balance_paise,
        "actions": [
            {
                "code": a.code,
                "label": a.label,
                "description": a.description,
                "posts_entry": a.posts_entry,
            }
            for a in exc.actions
        ],
        "trace": trace.as_dict() if trace is not None else None,
        "action_state": action,
    }


def exceptions_payload(run: Any) -> dict[str, Any]:
    """Everything the exception screen needs, in one response."""
    from api.routes.actions import action_state

    result: RunResult = run.result
    acted = run.trail.acted_subjects()

    problems = [e for e in result.exceptions if not e.is_in_transit]
    in_transit = [e for e in result.exceptions if e.is_in_transit]
    problems.sort(key=lambda e: (-(e.amount_paise or 0), e.ref))
    in_transit.sort(key=lambda e: (-(e.amount_paise or 0), e.ref))

    # Rows a human has already dealt with drop out of the balance but stay on
    # the page, struck through (frame 3a). Removing them entirely would make the
    # ledger discontinuous and lose the reversal.
    open_rows = [e for e in problems if e.ref not in acted]
    unreconciled = sum(e.amount_paise or 0 for e in open_rows)
    cleared = sum(e.amount_paise or 0 for e in problems if e.ref in acted)

    # The running balance: row N shows what is still open from N onward, so the
    # column descends to exactly 0.00 on the last row (frame 2a).
    #
    # Keyed by POSITION, not by ref. Refs are unique now that one credit yields
    # one card, but a map keyed by ref silently loses a row the moment that
    # stops being true — which is exactly how this column first shipped two
    # rows showing the same balance.
    balances: list[int] = []
    remaining = unreconciled
    for exc in open_rows:
        balances.append(remaining)
        remaining -= exc.amount_paise or 0
    by_ref = {id(exc): balance for exc, balance in zip(open_rows, balances, strict=True)}

    cards = [
        serialize_exception(
            e,
            open_balance_paise=by_ref.get(id(e)),
            trace=result.traces.get(e.ref),
            action=action_state(run, e.ref),
        )
        for e in problems
    ]

    position = result.cash_position
    in_transit_total = position.in_transit if position else 0
    in_transit_sum = sum(e.amount_paise or 0 for e in in_transit)

    return {
        "run_id": run.run_id,
        "label": run.label(),
        "seed": run.seed,
        "scale": run.scale,
        "closed": run.is_closed,
        # The header line: "₹1,50,918.37 across 9 exceptions" (§8.4).
        "open": len(open_rows),
        "unreconciled_paise": unreconciled,
        "unreconciled": str(Money(unreconciled)),
        "cleared_paise": cleared,
        # `remaining` is what is left after subtracting every row in order. It
        # is zero when the column ties, and any other number is a bug worth
        # seeing rather than hiding.
        "balance_ties": remaining == 0,
        "residual_paise": remaining,
        "auto_posted": position.entries_posted if position else 0,
        "pending_review": position.pending_review if position else 0,
        "started_at": run.started_at.isoformat(),
        "fee_rate": result.fee_rate,
        "exceptions": cards,
        # Filter chips, counted from the same list the rows come from so a chip
        # can never claim a count the page cannot show.
        "by_reason": _by_reason(problems),
        "in_transit": {
            "count": len(in_transit),
            "total_paise": in_transit_total,
            "total": str(Money(in_transit_total)),
            #: True when the line items add up to the total the books carry.
            #: Surfaced rather than assumed — a screen that quietly shows four
            #: rows under a total they do not reach is lying politely.
            "ties": in_transit_sum == in_transit_total,
            "items_paise": in_transit_sum,
            "items": [serialize_exception(e) for e in in_transit],
        },
    }


def _by_reason(problems: list[ExceptionOutcome]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for exc in problems:
        code = str(exc.reason_code)
        counts[code] = counts.get(code, 0) + 1
    return [
        {"reason_code": code, "count": count}
        for code, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
