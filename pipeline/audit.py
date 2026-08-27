"""Event bus -> audit trail. Observer pattern. Guide §5.3, §9.3.

Every accept, verdict, and action emits an event. Every posted entry must be
able to answer: WHO decided this, on WHAT evidence, WHEN, under WHICH prompt
version. In finance this is not optional.

Adding metrics collection means adding a subscriber, not editing the pipeline.

**The trail is the source of truth for what a human did**, which is why
`/books` reads "Resolved by hand 9, of those reversed 1" from here rather than
from a counter the frontend increments. A counter in a component is a number
that disagrees with the ledger the moment anything reloads; a projection over
recorded events cannot.

**The clock is injected.** §9.2 forbids business logic from reading the wall
clock — a trail that stamps itself from `datetime.now()` cannot be tested, and
two runs of the same seed would produce two different trails. `api/deps.py`
holds the one `SystemClock` permitted to read it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class EventType(StrEnum):
    """What happened. Values are stable — they are read back out of the trail."""

    #: A controller pressed an action button on an exception card (§8.3).
    ACTION_EXECUTED = "ACTION_EXECUTED"
    #: ...and then reversed it. The original stands; a correcting entry posts.
    ACTION_UNDONE = "ACTION_UNDONE"
    #: A prepared entry in the review queue was approved and posted (§4.5).
    REVIEW_APPROVED = "REVIEW_APPROVED"
    #: ...or rejected, which sends the credit back to the exception list.
    REVIEW_REJECTED = "REVIEW_REJECTED"
    #: The period was closed.
    RUN_CLOSED = "RUN_CLOSED"


class Clock(Protocol):
    """The one impure edge. Implemented by `api.deps.SystemClock`."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One thing that happened, and everything needed to defend it later."""

    event_type: EventType
    #: What it happened to: an exception ref, a UTR, a run id.
    subject: str
    actor: str
    at: datetime
    detail: str = ""
    #: Journal numbers this event caused to be written. A reversal names both
    #: the correcting entry and the one it corrects, in that order.
    entry_numbers: tuple[str, ...] = ()
    #: The action code, when the event came from a Command (§5.3).
    action_code: str = ""
    amount_paise: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "event_type": str(self.event_type),
            "subject": self.subject,
            "actor": self.actor,
            "at": self.at.isoformat(),
            "detail": self.detail,
            "entry_numbers": list(self.entry_numbers),
            "action_code": self.action_code,
            "amount_paise": self.amount_paise,
        }


Subscriber = Callable[[AuditEvent], None]


class AuditTrail:
    """Append-only event log, plus the projections the UI reads off it.

    Append-only on purpose: an audit trail you can edit is not one. Reversal is
    represented by a *second* event, never by removing the first — the same rule
    the ledger itself follows, and the reason the copy on the exception card
    says a reversal posts a correcting entry rather than deleting anything.
    """

    def __init__(self, clock: Clock, subscribers: Iterable[Subscriber] = ()) -> None:
        self._clock = clock
        self._events: list[AuditEvent] = []
        self._subscribers: list[Subscriber] = list(subscribers)

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def now(self) -> datetime:
        """The trail's clock, for callers that need to stamp something beside
        an event — closing a period, say. Exposed so nobody reaches for
        `datetime.now()` and reintroduces the leak §9.2 forbids."""
        return self._clock.now()

    def record(
        self,
        event_type: EventType,
        subject: str,
        *,
        actor: str,
        detail: str = "",
        entry_numbers: Iterable[str] = (),
        action_code: str = "",
        amount_paise: int | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=event_type,
            subject=subject,
            actor=actor,
            at=self._clock.now(),
            detail=detail,
            entry_numbers=tuple(entry_numbers),
            action_code=action_code,
            amount_paise=amount_paise,
        )
        self._events.append(event)
        for subscriber in self._subscribers:
            subscriber(event)
        return event

    # ------------------------------------------------------------ reading

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def for_subject(self, subject: str) -> tuple[AuditEvent, ...]:
        return tuple(e for e in self._events if e.subject == subject)

    def latest_for(self, subject: str) -> AuditEvent | None:
        for event in reversed(self._events):
            if event.subject == subject:
                return event
        return None

    def of_type(self, *types: EventType) -> tuple[AuditEvent, ...]:
        wanted = set(types)
        return tuple(e for e in self._events if e.event_type in wanted)

    # -------------------------------------------------------- projections

    def hand_resolution(self) -> HandResolution:
        """"Resolved by hand 9, of those reversed 1" (frame 3b).

        Computed over the events rather than counted as they arrive, so it is
        correct after a reload, correct if two tabs are open, and correct if
        somebody acts, reverses, and acts again. A subject that was acted on and
        then reversed counts in `reversed_count`, not in `resolved` — it is back
        on the worklist, and claiming it as resolved would overstate the close.
        """
        acted: dict[str, AuditEvent] = {}
        reversed_subjects: set[str] = set()
        for event in self._events:
            if event.event_type is EventType.ACTION_EXECUTED:
                acted[event.subject] = event
                reversed_subjects.discard(event.subject)
            elif event.event_type is EventType.ACTION_UNDONE:
                reversed_subjects.add(event.subject)

        still_resolved = {s: e for s, e in acted.items() if s not in reversed_subjects}
        return HandResolution(
            resolved=len(still_resolved),
            resolved_paise=sum(e.amount_paise or 0 for e in still_resolved.values()),
            reversed_count=len(reversed_subjects),
            reversed_paise=sum(
                e.amount_paise or 0
                for s, e in acted.items()
                if s in reversed_subjects
            ),
        )

    def review_outcome(self) -> ReviewOutcome:
        approved = self.of_type(EventType.REVIEW_APPROVED)
        rejected = self.of_type(EventType.REVIEW_REJECTED)
        return ReviewOutcome(
            approved=len(approved),
            approved_paise=sum(e.amount_paise or 0 for e in approved),
            rejected=len(rejected),
            rejected_paise=sum(e.amount_paise or 0 for e in rejected),
        )

    def acted_subjects(self) -> frozenset[str]:
        """Refs a human has acted on and not reversed — the rows frame 3a
        strikes through and drops out of the open balance."""
        # Replayed in order rather than differenced as two sets: an action
        # applied, reversed, then applied again is live, and a set difference
        # would call it reversed forever.
        live: set[str] = set()
        for event in self._events:
            if event.event_type is EventType.ACTION_EXECUTED:
                live.add(event.subject)
            elif event.event_type is EventType.ACTION_UNDONE:
                live.discard(event.subject)
        return frozenset(live)


@dataclass(frozen=True, slots=True)
class HandResolution:
    resolved: int = 0
    resolved_paise: int = 0
    reversed_count: int = 0
    reversed_paise: int = 0


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    approved: int = 0
    approved_paise: int = 0
    rejected: int = 0
    rejected_paise: int = 0


@dataclass
class EventBus:
    """Fan-out for anything that wants to watch a run. Guide §5.3.

    Kept separate from `AuditTrail` so that adding metrics collection means
    adding a subscriber here, not editing the pipeline.
    """

    subscribers: list[Subscriber] = field(default_factory=list)

    def subscribe(self, subscriber: Subscriber) -> None:
        self.subscribers.append(subscriber)

    def publish(self, event: AuditEvent) -> None:
        for subscriber in self.subscribers:
            subscriber(event)
