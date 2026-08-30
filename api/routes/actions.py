"""Executing and reversing an exception action. Guide §5.3, §8.3, frame 3a.

The buttons on a card come from `available_for(exc)` and nothing else. That is
the §8.3 rule, and it has a consequence worth stating because it is easy to get
backwards: **if the mock offers an action the registry does not return for that
reason code, the registry wins and the button disappears.** A design is a claim
about what should be possible; the registry is what is actually wired.

So this module never validates against a list of its own. It asks the registry
what is available, and refuses anything else — which means adding an action stays
one class plus one registry line, with no change here and none in the frontend.

**Reversal is a posting, not an erasure.** `undo` writes a correcting entry
beside the original; both stay in the ledger and both keep their journal numbers.
The copy on the card says exactly that, and it is not decoration — a controller
who believes the button deletes a posted entry has the wrong model of the system
and will eventually act on that belief.
"""

from __future__ import annotations

from typing import Any, cast

from core.money import Money
from core.run_result import ExceptionOutcome
from exceptions_.actions import ActionOutcome, action_for, available_for, execute, undo


class ActionNotAvailable(ValueError):
    """Asked for an action the registry does not offer on this reason code."""


class NothingToReverse(ValueError):
    """Asked to reverse a card nobody has acted on."""


class _Sink:
    """Where an executing action puts what it produces. Guide §5.3.

    The Command builds entries and asks the sink to post them; the sink owns the
    book and the trail. Keeping them apart is what lets the same action run in a
    test against an in-memory book and in the API against the run's book, with
    no branch inside the action.
    """

    def __init__(self, run: Any) -> None:
        self._run = run
        self.posted: list[tuple[str, Any]] = []
        self.notes: list[dict[str, str]] = []

    def post(self, entry: Any) -> bool:
        accepted = cast(bool, self._run.repository.post(entry))
        number = self._run.repository.number_for(entry.idempotency_key)
        if number:
            self.posted.append((number, entry))
        return accepted

    def note(self, ref: str, event: str, actor: str, detail: str) -> None:
        self.notes.append(
            {"ref": ref, "event": event, "actor": actor, "detail": detail}
        )


def perform(run: Any, ref: str, code: str, actor: str) -> dict[str, Any]:
    """Run one Command against one exception, and record what it did."""
    from pipeline.audit import EventType

    exc = _find(run, ref)
    offered = {a.code for a in available_for(exc)}
    if code not in offered:
        raise ActionNotAvailable(
            f"{code} is not available on {exc.reason_code} — "
            f"the registry offers {sorted(offered)}"
        )

    sink = _Sink(run)
    result = execute(code, exc, actor, sink)
    if result.is_err():
        raise ActionFailed(result.unwrap_err())

    outcome: ActionOutcome = result.unwrap()
    numbers = [number for number, _ in sink.posted]
    run.action_outcomes[ref] = outcome

    action = action_for(code)
    run.trail.record(
        EventType.ACTION_EXECUTED,
        ref,
        actor=actor,
        detail=outcome.detail or (action.label if action else code),
        entry_numbers=numbers,
        action_code=code,
        amount_paise=exc.amount_paise,
    )
    return {
        "ref": ref,
        "action": code,
        "state": "acted",
        "detail": outcome.detail,
        "entry_numbers": numbers,
        "entries": [
            {"number": number, "narration": entry.narration}
            for number, entry in sink.posted
        ],
        "amount_paise": exc.amount_paise,
        "amount": str(Money(exc.amount_paise or 0)),
        "reversible": True,
    }


def reverse(run: Any, ref: str, code: str, actor: str) -> dict[str, Any]:
    """Undo one Command — by posting against it, never by removing it."""
    from pipeline.audit import EventType

    exc = _find(run, ref)
    outcome = run.action_outcomes.get(ref)
    if outcome is None or ref not in run.trail.acted_subjects():
        raise NothingToReverse(f"{ref} has no action to reverse")

    sink = _Sink(run)
    result = undo(code, outcome, actor, sink)
    if result.is_err():
        raise ActionFailed(result.unwrap_err())

    correcting = [number for number, _ in sink.posted]
    original = list(getattr(outcome, "posted_keys", ()) or ())
    original_numbers = [
        n for n in (run.repository.number_for(k) for k in original) if n
    ]

    run.trail.record(
        EventType.ACTION_UNDONE,
        ref,
        actor=actor,
        # Correcting entry first, then what it corrects — the order the card
        # reads it back in: "JE-0065 against JE-0064".
        entry_numbers=[*correcting, *original_numbers],
        action_code=code,
        detail=(
            f"Reversed {code}: "
            f"{', '.join(correcting) or 'no entry'} posted against "
            f"{', '.join(original_numbers) or 'the original'}"
        ),
        amount_paise=exc.amount_paise,
    )
    return {
        "ref": ref,
        "action": code,
        "state": "reversed",
        "correcting_entries": correcting,
        "reverses": original_numbers,
        "reversible": False,
    }


def action_state(run: Any, ref: str) -> dict[str, Any] | None:
    """What frame 3a needs to draw a row that has been acted on.

    Returns None for an untouched row, which is the common case and should cost
    the card nothing.
    """
    from pipeline.audit import EventType

    events = [
        e
        for e in run.trail.for_subject(ref)
        if e.event_type in {EventType.ACTION_EXECUTED, EventType.ACTION_UNDONE}
    ]
    if not events:
        return None
    latest = events[-1]
    acted = latest.event_type is EventType.ACTION_EXECUTED
    action = action_for(latest.action_code)
    return {
        "state": "acted" if acted else "reversed",
        "action_code": latest.action_code,
        "action_label": action.label if action else latest.action_code,
        "actor": latest.actor,
        "at": latest.at.isoformat(),
        "detail": latest.detail,
        "entry_numbers": list(latest.entry_numbers),
        "reversible": acted,
    }


def audit_trail_payload(run: Any) -> dict[str, Any]:
    """"Export audit trail" (frame 3b).

    The whole log, in the order it happened, with the journal numbers each event
    caused. This is the artefact that answers "who decided this, on what
    evidence, when" (§9.3) — and the reason the counts on `/books` cannot drift
    away from what actually occurred.
    """
    hand = run.trail.hand_resolution()
    review = run.trail.review_outcome()
    return {
        "run_id": run.run_id,
        "label": run.label(),
        "seed": run.seed,
        "generated_at": run.trail.now().isoformat(),
        "events": [
            {**e.as_dict(), "actor_kind": "user"} for e in run.trail.events
        ],
        # Everything a machine decided, on the same actor axis as the human
        # events above. Without these the trail answers "who decided this" for
        # the handful of rows a person touched and stays silent about the rest.
        "decisions": _machine_decisions(run),
        "summary": {
            "resolved_by_hand": hand.resolved,
            "resolved_by_hand_paise": hand.resolved_paise,
            "reversed": hand.reversed_count,
            "reversed_paise": hand.reversed_paise,
            "approved_in_review": review.approved,
            "rejected_in_review": review.rejected,
        },
    }


#: strategy -> the actor label the trail shows it under.
_ACTOR: dict[str, str] = {
    "L1_exact": "system:L1",
    "L3_subset": "system:L3",
    "L4_adjudicate": "llm",
}


def _machine_decisions(run: Any) -> list[dict[str, Any]]:
    """Every match the run made, as an audit line.

    `evidence` is the list of fields the matcher actually joined on, which is
    the "on what evidence" half of the question — a confidence with no
    evidence behind it is a number, not a justification.
    """
    result = run.result
    at = run.started_at.isoformat()
    out: list[dict[str, Any]] = []

    if result.fee_rate is not None:
        settled = sum(1 for m in result.matches.values() if m.strategy == "L1_exact")
        out.append(
            {
                "actor": "system:L2",
                "actor_kind": "system",
                "at": at,
                "subject": run.run_id,
                "detail": (
                    f"inferred fee rate {result.fee_rate * 100:.4f}% "
                    f"from {settled} settlements"
                ),
                "evidence": [],
                "confidence": None,
            }
        )

    for utr, match in sorted(result.matches.items()):
        actor = _ACTOR.get(match.strategy, match.strategy)
        out.append(
            {
                "actor": actor,
                "actor_kind": "llm" if actor == "llm" else "system",
                "at": at,
                "subject": utr,
                "detail": (
                    f"matched {utr} -> {len(match.ledger_ids)} order"
                    f"{'' if len(match.ledger_ids) == 1 else 's'} · "
                    f"conf {match.confidence:.2f}"
                ),
                "evidence": list(match.evidence),
                "confidence": match.confidence,
            }
        )

    # "The model was asked and declined" is a decision, and one the trail has
    # no other way to show — the credit simply stays an exception.
    for note in result.adjudication_notes:
        out.append(
            {
                "actor": "llm",
                "actor_kind": "llm",
                "at": at,
                "subject": run.run_id,
                "detail": note,
                "evidence": [],
                "confidence": None,
            }
        )

    return out


def _find(run: Any, ref: str) -> ExceptionOutcome:
    for exc in run.result.exceptions:
        if exc.ref == ref:
            return cast(ExceptionOutcome, exc)
    raise KeyError(ref)


class ActionFailed(RuntimeError):
    """The Command declined. Its reason is data and goes back to the caller."""
