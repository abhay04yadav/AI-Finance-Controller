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

from datetime import date
from typing import Any

from core.money import Money
from core.reason_codes import ReasonCode, plain_english_of, title_of
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


#: How the open card opens, per reason code. Design 4a leads every card with a
#: sentence a person can read before any jargon — "₹24,860 arrived in the bank.
#: We can't work out which orders it paid for." The amount is substituted, so
#: the sentence is about THIS credit rather than about the category.
#:
#: Built here rather than in the component for the same reason `plain` is built
#: in `core/`: a screen must never be able to describe a code the backend does
#: not know about, and a template living in JSX is a template nobody tests.
_HEADLINES: dict[ReasonCode, str] = {
    ReasonCode.AMOUNT_MISMATCH: (
        "{amount} arrived in the bank. We can't work out which orders it paid for."
    ),
    ReasonCode.DUPLICATE_UTR: (
        "{amount} appears twice on the statement. Only one of them is real money."
    ),
    ReasonCode.MISSING_IN_LEDGER: (
        "{amount} arrived for sales the books never recorded."
    ),
    ReasonCode.AMBIGUOUS_UNADJUDICATED: (
        "{amount} arrived. Several different sets of orders explain it equally "
        "well, and nothing separates them."
    ),
    ReasonCode.ADJUDICATION_REJECTED: (
        "{amount} arrived. An adjudicator picked an answer and it failed its "
        "checks, so nothing was matched."
    ),
    ReasonCode.AUTO_REFUNDED: (
        "{amount} was authorised but never captured, so it went back to the "
        "customer."
    ),
    ReasonCode.CROSS_PERIOD_REFUND: (
        "{amount} is a refund against a month whose books are already closed."
    ),
    ReasonCode.HOLIDAY_SHIFT: (
        "{amount} landed later than expected because a bank holiday moved it."
    ),
    ReasonCode.LATE_AUTHORIZATION: (
        "{amount} was charged well after the customer agreed to pay."
    ),
    ReasonCode.ROUNDING_DRIFT: (
        "{amount} is off by an amount too small to be a mistake and too "
        "consistent to be noise."
    ),
    ReasonCode.FX_OR_SLAB_VARIANCE: (
        "{amount} arrived short. The gateway kept a bigger share than our "
        "model expects."
    ),
    ReasonCode.AWAITING_SETTLEMENT: (
        "{amount} has left the customer and is on its way to the bank."
    ),
    ReasonCode.INGEST_ERROR: "A row in the source file could not be read.",
}


def headline_for(exc: ExceptionOutcome) -> str:
    """The sentence the open card leads with (design 4a)."""
    template = _HEADLINES.get(exc.reason_code)
    if template is None:
        return plain_english_of(exc.reason_code)
    return template.format(amount=str(Money(exc.amount_paise or 0)))


def _age_days(exc: ExceptionOutcome, run_at: date | None) -> int | None:
    """How long this credit has been sitting there, in whole days.

    A date subtraction over two facts the run already holds. Computed here
    rather than in the browser so the ledger and the "oldest" tile cannot
    disagree about which row is the stale one.
    """
    if exc.value_date is None or run_at is None:
        return None
    days = (run_at - exc.value_date).days
    return days if days >= 0 else None


def _signal(exc: ExceptionOutcome, trace: Any) -> str:
    """The SIGNAL column: how many ways this could be read, and who read them.

    Three states, and they are genuinely different: `unadjudicated` means L4
    was asked and declined, `adjudicated` means it answered, `needs review`
    means it was never reached. Collapsing them would hide the one fact this
    screen exists to be honest about — whether a model was involved.
    """
    candidates = len(getattr(trace, "candidates", ()) or ()) if trace is not None else 0
    # A credit the solver found no valid combination for still had rows put in
    # front of it, and "0 candidates" throws that away — it reads as "we did
    # not look". The node count is what was actually considered.
    considered = len(getattr(trace, "nodes", ()) or ()) if trace is not None else 0
    noun = "candidate"
    if candidates:
        n = candidates
        if exc.reason_code is ReasonCode.AUTO_REFUNDED:
            noun = "refund"
    elif considered:
        n, noun = considered, "row"
    else:
        # Nothing was weighed because there was nothing to weigh — a refund
        # needs no candidate search. "0 refunds" reads as "we did not look",
        # which is the opposite of what happened.
        n, noun = 1, "record"
    unit = f"{n} {noun}" if n == 1 else f"{n} {noun}s"
    if exc.confidence is not None:
        return f"{unit} · adjudicated"
    if exc.reason_code is ReasonCode.AMBIGUOUS_UNADJUDICATED:
        return f"{unit} · unadjudicated"
    return f"{unit} · needs review"


def _layers(exc: ExceptionOutcome, trace: Any, fee_rate: float | None) -> list[dict[str, str]]:
    """"How this was decided", layer by layer.

    Each line is a fact about THIS credit, read off the trace: L1 never fires
    on an exception (it would have matched), L2 either inferred a rate or did
    not, L3 either generated candidates or found none, and L4 either answered,
    declined, or was never reached.
    """
    candidates = len(getattr(trace, "candidates", ()) or ()) if trace is not None else 0
    rows = [
        {
            "layer": "L1 exact join",
            "state": "ok",
            "note": "not applicable — no shared identifier",
        },
        {
            "layer": "L2 fee model",
            "state": "ok" if fee_rate is not None else "warn",
            "note": (
                f"verified · {fee_rate * 100:.4f}% inferred"
                if fee_rate is not None
                else "no rate could be inferred"
            ),
        },
        {
            "layer": "L3 candidate generation",
            "state": "ok" if candidates else "warn",
            "note": (
                f"{candidates} candidate{'' if candidates == 1 else 's'}, "
                "all arithmetically valid"
                if candidates
                else "no combination reached this credit"
            ),
        },
    ]
    if exc.confidence is not None:
        rows.append(
            {
                "layer": "L4 adjudication",
                "state": "ok",
                "note": f"answered · confidence {exc.confidence:.2f}",
            }
        )
    elif exc.reason_code is ReasonCode.AMBIGUOUS_UNADJUDICATED:
        rows.append(
            {
                "layer": "L4 adjudication",
                "state": "warn",
                "note": "model declined to choose",
            }
        )
    else:
        rows.append(
            {"layer": "L4 adjudication", "state": "off", "note": "not reached"}
        )
    return rows


def _posts_preview(offer: Any, amount_paise: int | None) -> str:
    """"posts  Dr Bank 24,860.00 · Cr Suspense 24,860.00", from the action's
    own declared shape. An action that writes nothing says so."""
    if not offer.posting_shape:
        return "no entry posted"
    amount = str(Money(amount_paise or 0))
    parts = [f"{side} {account} {amount}" for side, account, _which in offer.posting_shape]
    return " · ".join(parts)


def serialize_exception(
    exc: ExceptionOutcome,
    *,
    open_balance_paise: int | None = None,
    trace: Any = None,
    action: dict[str, Any] | None = None,
    run_at: date | None = None,
    fee_rate: float | None = None,
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
        # Design 4a: a plain sentence on every row, and an amount-aware
        # headline on the one that is open. Neither replaces WHAT or WHY —
        # they are what a controller reads FIRST, before the jargon.
        "plain": plain_english_of(exc.reason_code),
        "headline": headline_for(exc),
        # P0: the short label a row leads with, the at-a-glance signal, and how
        # long this has been sitting there — so a closed row says something
        # without being opened.
        "title": title_of(exc.reason_code),
        "signal": _signal(exc, trace),
        "age_days": _age_days(exc, run_at),
        "confidence": exc.confidence,
        "layers": _layers(exc, trace, fee_rate),
        # The pipeline puts the action it suggests first (see
        # `adjudication_step._action_first`), so "recommended" is a fact about
        # ordering rather than a second opinion held on this side.
        "recommended_action": exc.actions[0].code if exc.actions else None,
        "open_balance_paise": open_balance_paise,
        "actions": [
            {
                "code": a.code,
                "label": a.label,
                "description": a.description,
                "posts_entry": a.posts_entry,
                "posts_preview": _posts_preview(a, exc.amount_paise),
                # The same shape as structured lines, so a posted card can
                # render the entry as a table rather than re-parsing the
                # preview string it was given for the button.
                "posting_lines": [
                    {
                        "side": side,
                        "account": account,
                        "amount_paise": exc.amount_paise or 0,
                    }
                    for side, account, _which in a.posting_shape
                ],
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

    run_at = run.started_at.date()
    cards = [
        serialize_exception(
            e,
            open_balance_paise=by_ref.get(id(e)),
            trace=result.traces.get(e.ref),
            action=action_state(run, e.ref),
            run_at=run_at,
            fee_rate=result.fee_rate,
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
        # Design 4a's stacked bar: where the unreconciled total actually sits.
        # A chart reads faster than nine numbers, but only if it is the SAME
        # nine numbers — so the shares are computed from `problems` here rather
        # than re-derived in the browser from rounded percentages.
        "composition": _composition(problems, unreconciled + cleared),
        # P0: the funnel across the whole run — what the deterministic core
        # finished, what a person still owes an answer on, and what is left
        # open. The four numbers are the same 605 records seen from the top.
        "funnel": _funnel(run, result, open_rows, unreconciled),
        # The four tiles above the chips. Aggregates over the SAME `open_rows`
        # the ledger renders, so a tile can never name a row the list does not
        # contain.
        "highlights": _highlights(open_rows, run_at),
        # What ran with no model at all, and what the model was allowed to do.
        "ai_mode": _ai_mode(run, result),
        "in_transit": {
            "count": len(in_transit),
            "total_paise": in_transit_total,
            "total": str(Money(in_transit_total)),
            #: True when the line items add up to the total the books carry.
            #: Surfaced rather than assumed — a screen that quietly shows four
            #: rows under a total they do not reach is lying politely.
            "ties": in_transit_sum == in_transit_total,
            "items_paise": in_transit_sum,
            "items": [
                serialize_exception(e, run_at=run_at, fee_rate=result.fee_rate)
                for e in in_transit
            ],
        },
    }


def _funnel(
    run: Any, result: Any, open_rows: list[ExceptionOutcome], unreconciled: int
) -> list[dict[str, Any]]:
    """Where every record went, as segments of one bar.

    Counted by the strategy that produced each match, so "L1 exact 48" is the
    number of credits the exact matcher actually closed rather than a share
    back-computed from a percentage.

    The segments are DISJOINT and sum to the record count. The design draws
    L1, L3, Review and Open side by side, but on this run L3's eleven credits
    ARE the eleven in review — drawing both would show 77 segments of a
    66-record run and the bar would overstate the work by a sixth. Each match
    appears once, under the layer that produced it, with where it went as a
    note.
    """
    from api.routes.review import cash_awaiting

    review = list(result.review_queue)
    review_refs = {item.utr for item in review}
    review_cash = {item.utr: cash_awaiting(item) for item in review}

    buckets: dict[str, dict[str, Any]] = {}
    for match in result.matches.values():
        key = {"L1_exact": "L1", "L3_subset": "L3", "L4_adjudicate": "L4"}.get(
            match.strategy, match.strategy
        )
        b = buckets.setdefault(
            key, {"key": key, "count": 0, "paise": 0, "in_review": 0}
        )
        b["count"] += 1
        if match.utr in review_refs:
            b["in_review"] += 1
            b["paise"] += review_cash.get(match.utr, 0)

    labels = {"L1": "L1 exact", "L3": "L3 subset", "L4": "L4 adjudicated"}
    segments: list[dict[str, Any]] = []
    for key in ("L1", "L3", "L4"):
        b = buckets.get(key)
        if not b or not b["count"]:
            continue
        waiting = b["in_review"]
        segments.append(
            {
                "key": key,
                "label": labels[key],
                "count": b["count"],
                "paise": b["paise"],
                "note": (
                    f"{waiting} awaiting review"
                    if waiting == b["count"]
                    else f"{waiting} awaiting review"
                    if waiting
                    else "posted without review"
                ),
            }
        )
    if open_rows:
        segments.append(
            {
                "key": "OPEN",
                "label": "Open",
                "count": len(open_rows),
                "paise": unreconciled,
                "note": "needs a decision",
            }
        )

    total = sum(s["count"] for s in segments) or 1
    for seg in segments:
        seg["share"] = seg["count"] / total
    return segments


def _highlights(open_rows: list[ExceptionOutcome], run_at: date) -> dict[str, Any]:
    """Largest, oldest, most common, and how many need a person.

    Every tile names a row that is in the list below it — these are reductions
    over `open_rows`, not a second query.
    """
    if not open_rows:
        return {}

    largest = max(open_rows, key=lambda e: e.amount_paise or 0)
    dated = [e for e in open_rows if e.value_date is not None]
    oldest = min(dated, key=lambda e: e.value_date) if dated else None

    counts: dict[ReasonCode, int] = {}
    for e in open_rows:
        counts[e.reason_code] = counts.get(e.reason_code, 0) + 1
    top = max(counts.values())
    tied = sorted(code for code, n in counts.items() if n == top)

    return {
        "largest": {
            "amount_paise": largest.amount_paise,
            "amount": str(Money(largest.amount_paise or 0)),
            "reason_code": str(largest.reason_code),
        },
        "oldest": (
            {
                "value_date": oldest.value_date.isoformat(),
                "age_days": (run_at - oldest.value_date).days,
                "reason_code": str(oldest.reason_code),
            }
            if oldest is not None
            else None
        ),
        "most_common": {
            "reason_code": str(tied[0]),
            "count": top,
            # "tied with 2 other codes" is a different statement from "the most
            # common code", and on a seven-row list the tie is the common case.
            "tied_with": len(tied) - 1,
        },
        "needs_human": {"count": len(open_rows), "of": len(open_rows)},
    }


def _all_balanced(entries: Any) -> bool:
    """Debits equal credits on every entry. Stated as a fact the screen can
    print, not asserted — a page that raises rather than reporting an
    unbalanced book tells the controller nothing."""
    return all(e.total_debits == e.total_credits for e in entries)


def _ai_mode(run: Any, result: Any) -> dict[str, Any]:
    """What ran with no model at all, and what the model was allowed to do.

    The claim this screen makes — that the model is the last mile, not the
    engine — is only worth making if the numbers behind it are the run's own.
    """
    matches = list(result.matches.values())
    by_strategy: dict[str, list[Any]] = {}
    for m in matches:
        by_strategy.setdefault(m.strategy, []).append(m)

    deterministic = [
        {
            "layer": "L0 ingest",
            "detail": f"{result.records_processed} records",
        },
        {
            "layer": "L1 exact matching",
            "detail": f"{len(by_strategy.get('L1_exact', []))} credits",
        },
        {
            "layer": "L2 fee inference",
            "detail": (
                f"{result.fee_rate * 100:.4f}% from "
                f"{len(by_strategy.get('L1_exact', []))} settlements"
                if result.fee_rate is not None
                else "no rate inferred"
            ),
        },
        {
            "layer": "L3 candidate generation",
            "detail": f"{len(by_strategy.get('L3_subset', []))} credits",
        },
        {
            "layer": "L5 journal validation",
            "detail": (
                f"{len(result.entries)} entries, "
                f"{'all balanced' if _all_balanced(result.entries) else 'NOT all balanced'}"
            ),
        },
    ]

    total = result.records_processed or 1
    return {
        "deterministic": deterministic,
        "llm_calls": result.llm_calls,
        "llm_cost_paise": result.llm_cost_paise,
        "llm_share": result.llm_calls / total,
        "adjudicated": len(by_strategy.get("L4_adjudicate", [])),
        "notes": list(result.adjudication_notes),
    }


def _composition(
    problems: list[ExceptionOutcome], total: int
) -> list[dict[str, Any]]:
    """One segment per exception, largest first, with its share of the total.

    Per EXCEPTION, not per reason code: two ROUNDING_DRIFT rows are two
    segments, because the bar is a picture of the money and a controller
    reading it should see two separate problems rather than one wide band.
    The legend groups by code; the bar does not.

    `share` is a fraction, not a percentage string — the page formats it, and
    the two must not disagree about rounding.
    """
    if total <= 0:
        return []
    return [
        {
            "ref": exc.ref,
            "reason_code": str(exc.reason_code),
            "paise": exc.amount_paise or 0,
            "share": (exc.amount_paise or 0) / total,
        }
        for exc in problems
    ]


def _by_reason(problems: list[ExceptionOutcome]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for exc in problems:
        code = str(exc.reason_code)
        counts[code] = counts.get(code, 0) + 1
    amounts: dict[str, int] = {}
    for exc in problems:
        code = str(exc.reason_code)
        amounts[code] = amounts.get(code, 0) + (exc.amount_paise or 0)
    total = sum(amounts.values())
    return [
        {
            "reason_code": code,
            "count": count,
            "paise": amounts[code],
            "share": amounts[code] / total if total else 0.0,
        }
        for code, count in sorted(counts.items(), key=lambda kv: (-amounts[kv[0]], kv[0]))
    ]
