"""Exception actions as Commands. Guide §8.3, §5.3.

**ACTION is what closes the loop.** Without it this is a report; with it, it is
a controller. Every exception card offers the things a human can actually do
about it, and each one is an object with `execute()` and `undo()` rather than a
button wired to a handler somewhere in the frontend.

Three consequences fall out of that:

* The UI renders whatever `available_for()` returns. There is no hardcoded
  button list, so adding an action is one class and one registry line, with no
  frontend change at all (§8.3).
* Everything is reversible. A controller who posts the wrong credit to suspense
  can take it back, and the reversal is itself an entry rather than a deletion —
  books are never rewritten, only added to.
* Every execution returns an `ActionOutcome` carrying enough to undo it and
  enough to audit it: who did what, to which reference, when (§9.3).

Actions never touch a database directly. They build journal entries and hand
them to an `ActionSink`, which the pipeline supplies. That keeps this package
importing only `core/` and `posting/`, and makes every action testable without
persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from core.models import JournalEntry
from core.money import Money
from core.reason_codes import ReasonCode
from core.result import Err, Ok, Result
from core.run_result import ActionOffer, ExceptionOutcome
from posting.chart_of_accounts import Account
from posting.journal_builder import JournalEntryBuilder

# --------------------------------------------------------------------------
# What an action needs from the outside world
# --------------------------------------------------------------------------


class ActionSink(Protocol):
    """Where an action's effects land.

    Narrow on purpose: an action can post an entry, reverse one, and record that
    it happened. It cannot query the books, delete anything, or reach the
    matcher — so no action can quietly change a number it was not asked to.
    """

    def post(self, entry: JournalEntry) -> bool: ...

    def note(self, ref: str, event: str, actor: str, detail: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    """What an action did, in enough detail to reverse and to audit."""

    action_code: str
    ref: str
    actor: str
    detail: str
    posted_keys: tuple[str, ...] = ()
    entries: tuple[JournalEntry, ...] = field(default=(), compare=False)

    @property
    def posted(self) -> bool:
        return bool(self.posted_keys)


class ExceptionAction(Protocol):
    """One thing a controller can do about an exception."""

    code: str
    label: str
    description: str
    posts_entry: bool

    def is_available(self, exc: ExceptionOutcome) -> bool: ...

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]: ...

    def undo(self, outcome: ActionOutcome, actor: str, sink: ActionSink) -> Result[None, str]: ...


# --------------------------------------------------------------------------
# Shared behaviour
# --------------------------------------------------------------------------


class _BaseAction:
    """Defaults every action shares. Not a fat base class — four attributes and
    two helpers, and any action may override all of them (§5.3 rejects a
    `BaseMatcher`-style hierarchy for the same reason)."""

    code = ""
    label = ""
    description = ""
    posts_entry = False
    #: Reason codes this action makes sense for.
    applies_to: frozenset[ReasonCode] = frozenset()

    def is_available(self, exc: ExceptionOutcome) -> bool:
        return exc.reason_code in self.applies_to

    def offer(self) -> ActionOffer:
        return ActionOffer(
            code=self.code,
            label=self.label,
            description=self.description,
            posts_entry=self.posts_entry,
        )

    def _note_only(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink, detail: str
    ) -> Result[ActionOutcome, str]:
        sink.note(exc.ref, self.code, actor, detail)
        return Ok(ActionOutcome(self.code, exc.ref, actor, detail))

    def undo(
        self, outcome: ActionOutcome, actor: str, sink: ActionSink
    ) -> Result[None, str]:
        """Reverse a note-only action. Posting actions override this."""
        if outcome.posted:
            return Err(f"{self.code} posted entries and must override undo()")
        sink.note(outcome.ref, f"UNDO_{self.code}", actor, outcome.detail)
        return Ok(None)


def _reversal_of(entry: JournalEntry, actor: str) -> JournalEntry:
    """A mirror-image entry. Books are appended to, never rewritten.

    Deleting an entry would leave the audit trail lying about what the books
    once said; a reversal leaves both facts visible, which is what an auditor
    expects to see (§9.3).
    """
    builder = (
        JournalEntryBuilder()
        .on(entry.entry_date)
        .because(f"Reversal of {entry.narration} (by {actor})")
    )
    for line in entry.lines:
        if line.debit_paise:
            builder = builder.credit(Account(line.account), line.debit_paise)
        else:
            builder = builder.debit(Account(line.account), line.credit_paise)
    return builder.traced_to(
        key=f"reverse:{entry.idempotency_key}",
        source_utr=entry.source_utr,
        ledger_ids=entry.ledger_ids,
        settlement_id=entry.settlement_id,
    ).build()


# --------------------------------------------------------------------------
# Actions that write to the books
# --------------------------------------------------------------------------


class PostToSuspense(_BaseAction):
    """Park unexplained money where the books still tie to the bank."""

    code = "POST_TO_SUSPENSE"
    label = "Post to suspense"
    description = (
        "Records the credit against the suspense account so the bank balance in "
        "the books matches the statement while the cause is investigated."
    )
    posts_entry = True
    applies_to = frozenset(
        {
            ReasonCode.MISSING_IN_LEDGER,
            ReasonCode.AMOUNT_MISMATCH,
            ReasonCode.ADJUDICATION_REJECTED,
            ReasonCode.AMBIGUOUS_UNADJUDICATED,
            ReasonCode.FX_OR_SLAB_VARIANCE,
            ReasonCode.DUPLICATE_UTR,
        }
    )

    def is_available(self, exc: ExceptionOutcome) -> bool:
        return super().is_available(exc) and bool(exc.amount_paise)

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        amount = exc.amount_paise or 0
        if amount <= 0:
            return Err("nothing to post: the exception carries no amount")
        entry = (
            JournalEntryBuilder()
            .debit(Account.BANK, amount)
            .credit(Account.SUSPENSE, amount)
            .on(exc.value_date or date.today())
            .because(f"Unreconciled credit {exc.ref} — {exc.reason_code}")
            .traced_to(key=f"{self.code}:{exc.ref}", source_utr=exc.ref)
            .build()
        )
        if not sink.post(entry):
            return Err(f"{exc.ref} has already been posted to suspense")
        sink.note(exc.ref, self.code, actor, f"{Money(amount)} to suspense")
        return Ok(
            ActionOutcome(
                self.code,
                exc.ref,
                actor,
                f"{Money(amount)} parked in suspense",
                posted_keys=(entry.idempotency_key,),
                entries=(entry,),
            )
        )

    def undo(
        self, outcome: ActionOutcome, actor: str, sink: ActionSink
    ) -> Result[None, str]:
        for entry in outcome.entries:
            if not sink.post(_reversal_of(entry, actor)):
                return Err(f"{outcome.ref} has already been reversed")
        sink.note(outcome.ref, f"UNDO_{self.code}", actor, outcome.detail)
        return Ok(None)


class AcceptWithWriteOff(_BaseAction):
    """Accept a sub-rupee difference and book the remainder."""

    code = "ACCEPT_WITH_WRITEOFF"
    label = "Accept with write-off"
    description = (
        "Books the few paise the fee model cannot account for to the rounding "
        "write-off account and treats the match as settled."
    )
    posts_entry = True
    applies_to = frozenset(
        {ReasonCode.ROUNDING_DRIFT, ReasonCode.FX_OR_SLAB_VARIANCE}
    )

    #: Above this the difference is not rounding, and writing it off would hide
    #: a real discrepancy (§4.2 rounding note).
    MAX_WRITEOFF_PAISE = 5_000

    def is_available(self, exc: ExceptionOutcome) -> bool:
        amount = abs(exc.amount_paise or 0)
        return super().is_available(exc) and 0 < amount <= self.MAX_WRITEOFF_PAISE

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        amount = exc.amount_paise or 0
        if abs(amount) > self.MAX_WRITEOFF_PAISE:
            return Err(
                f"{Money(abs(amount))} is too large to write off as rounding — "
                "this is a real discrepancy, not drift"
            )
        entry = (
            JournalEntryBuilder()
            .debit(Account.ROUNDING_WRITEOFF, amount)
            .credit(Account.SUSPENSE, amount)
            .on(exc.value_date or date.today())
            .because(f"Rounding write-off for {exc.ref}")
            .traced_to(key=f"{self.code}:{exc.ref}", source_utr=exc.ref)
            .build()
        )
        if not sink.post(entry):
            return Err(f"{exc.ref} has already been written off")
        sink.note(exc.ref, self.code, actor, f"{Money(amount)} written off")
        return Ok(
            ActionOutcome(
                self.code,
                exc.ref,
                actor,
                f"{Money(amount)} written off as rounding",
                posted_keys=(entry.idempotency_key,),
                entries=(entry,),
            )
        )

    def undo(
        self, outcome: ActionOutcome, actor: str, sink: ActionSink
    ) -> Result[None, str]:
        for entry in outcome.entries:
            if not sink.post(_reversal_of(entry, actor)):
                return Err(f"{outcome.ref} has already been reversed")
        sink.note(outcome.ref, f"UNDO_{self.code}", actor, outcome.detail)
        return Ok(None)


class CreateLedgerEntry(_BaseAction):
    """Record revenue the books never captured."""

    code = "CREATE_LEDGER_ENTRY"
    label = "Create ledger entry"
    description = (
        "Recognises the sale the gateway settled but the books never recorded, "
        "so unrecorded revenue stops being an audit finding."
    )
    posts_entry = True
    applies_to = frozenset(
        {ReasonCode.MISSING_IN_LEDGER, ReasonCode.LATE_AUTHORIZATION}
    )

    def is_available(self, exc: ExceptionOutcome) -> bool:
        return super().is_available(exc) and bool(exc.amount_paise)

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        amount = exc.amount_paise or 0
        if amount <= 0:
            return Err("nothing to recognise: the exception carries no amount")
        entry = (
            JournalEntryBuilder()
            .debit(Account.BANK, amount)
            .credit(Account.ACCOUNTS_RECEIVABLE, amount)
            .on(exc.value_date or date.today())
            .because(f"Unrecorded revenue recognised for {exc.ref}")
            .traced_to(key=f"{self.code}:{exc.ref}", source_utr=exc.ref)
            .build()
        )
        if not sink.post(entry):
            return Err(f"a ledger entry already exists for {exc.ref}")
        sink.note(exc.ref, self.code, actor, f"{Money(amount)} recognised")
        return Ok(
            ActionOutcome(
                self.code,
                exc.ref,
                actor,
                f"{Money(amount)} of unrecorded revenue recognised",
                posted_keys=(entry.idempotency_key,),
                entries=(entry,),
            )
        )

    def undo(
        self, outcome: ActionOutcome, actor: str, sink: ActionSink
    ) -> Result[None, str]:
        for entry in outcome.entries:
            if not sink.post(_reversal_of(entry, actor)):
                return Err(f"{outcome.ref} has already been reversed")
        sink.note(outcome.ref, f"UNDO_{self.code}", actor, outcome.detail)
        return Ok(None)


# --------------------------------------------------------------------------
# Actions that record a decision without moving money
# --------------------------------------------------------------------------


class MarkInTransit(_BaseAction):
    code = "MARK_IN_TRANSIT"
    label = "Mark in-transit"
    description = (
        "Confirms the money is on its way and keeps it out of the exception "
        "list until the settlement window closes."
    )
    applies_to = frozenset({ReasonCode.AWAITING_SETTLEMENT})

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        return self._note_only(
            exc, actor, sink, f"{exc.ref} confirmed in transit"
        )


class SnoozeToExpectedDate(_BaseAction):
    code = "SNOOZE"
    label = "Snooze to expected date"
    description = (
        "Hides the item until the date it is expected to settle, so it stops "
        "occupying attention it does not need yet."
    )
    applies_to = frozenset(
        {ReasonCode.AWAITING_SETTLEMENT, ReasonCode.LATE_AUTHORIZATION}
    )

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        return self._note_only(exc, actor, sink, f"{exc.ref} snoozed")


class IgnoreDuplicate(_BaseAction):
    code = "IGNORE_DUPLICATE"
    label = "Ignore duplicate"
    description = (
        "Marks the repeated bank line as a statement artefact so it is never "
        "matched, which is what stops the revenue being posted twice."
    )
    applies_to = frozenset({ReasonCode.DUPLICATE_UTR})

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        return self._note_only(
            exc, actor, sink, f"{exc.ref} marked as a duplicate statement line"
        )


class RaiseGatewayTicket(_BaseAction):
    code = "RAISE_GATEWAY_TICKET"
    label = "Raise gateway ticket"
    description = (
        "Escalates to the payment gateway with the reference, amount and date "
        "already filled in."
    )
    applies_to = frozenset(
        {
            ReasonCode.DUPLICATE_UTR,
            ReasonCode.AMOUNT_MISMATCH,
            ReasonCode.FX_OR_SLAB_VARIANCE,
            ReasonCode.MISSING_IN_LEDGER,
            ReasonCode.CROSS_PERIOD_REFUND,
        }
    )

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        amount = Money(exc.amount_paise) if exc.amount_paise else "unknown amount"
        return self._note_only(
            exc, actor, sink, f"Ticket raised for {exc.ref} ({amount})"
        )


class ManualMatch(_BaseAction):
    code = "MANUAL_MATCH"
    label = "Match manually"
    description = (
        "Lets a controller name the ledger rows themselves when arithmetic "
        "could not decide between them."
    )
    applies_to = frozenset(
        {
            ReasonCode.AMOUNT_MISMATCH,
            ReasonCode.ADJUDICATION_REJECTED,
            ReasonCode.AMBIGUOUS_UNADJUDICATED,
            ReasonCode.ROUNDING_DRIFT,
            ReasonCode.CROSS_PERIOD_REFUND,
            ReasonCode.HOLIDAY_SHIFT,
            ReasonCode.FX_OR_SLAB_VARIANCE,
        }
    )

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        return self._note_only(
            exc, actor, sink, f"{exc.ref} queued for manual matching"
        )


class RerunReconciliation(_BaseAction):
    code = "RERUN"
    label = "Re-run reconciliation"
    description = (
        "Re-runs the match now that later data has arrived — a payment "
        "authorised after the fact often resolves on the next pass."
    )
    applies_to = frozenset(
        {
            ReasonCode.LATE_AUTHORIZATION,
            ReasonCode.HOLIDAY_SHIFT,
            ReasonCode.AWAITING_SETTLEMENT,
        }
    )

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        return self._note_only(exc, actor, sink, f"Re-run requested for {exc.ref}")


class WriteOffAsRefunded(_BaseAction):
    code = "WRITE_OFF_AS_REFUNDED"
    label = "Write off as refunded"
    description = (
        "Closes a sale that was authorised but never captured, so the books "
        "stop showing revenue that will never arrive."
    )
    applies_to = frozenset({ReasonCode.AUTO_REFUNDED})

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        return self._note_only(
            exc, actor, sink, f"{exc.ref} closed — auto-refunded to the customer"
        )


class Escalate(_BaseAction):
    code = "ESCALATE"
    label = "Escalate"
    description = "Hands the item to a senior reviewer with its full trail attached."
    #: The catch-all, so no reason code can ever have an empty action list.
    applies_to = frozenset(ReasonCode)

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        return self._note_only(exc, actor, sink, f"{exc.ref} escalated")


class CorrectSourceRow(_BaseAction):
    code = "CORRECT_SOURCE_ROW"
    label = "Correct the source row"
    description = (
        "Opens the offending row so the export can be fixed and re-ingested. "
        "The row is never repaired automatically."
    )
    applies_to = frozenset({ReasonCode.INGEST_ERROR})

    def execute(
        self, exc: ExceptionOutcome, actor: str, sink: ActionSink
    ) -> Result[ActionOutcome, str]:
        return self._note_only(
            exc, actor, sink, f"{exc.ref} flagged for source correction"
        )


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

#: Order matters: the first is the one a controller most likely wants, and the
#: UI renders them in this order. Escalate is last everywhere by construction.
REGISTRY: tuple[ExceptionAction, ...] = (
    MarkInTransit(),
    SnoozeToExpectedDate(),
    IgnoreDuplicate(),
    CreateLedgerEntry(),
    AcceptWithWriteOff(),
    ManualMatch(),
    RerunReconciliation(),
    WriteOffAsRefunded(),
    RaiseGatewayTicket(),
    PostToSuspense(),
    CorrectSourceRow(),
    Escalate(),
)

_BY_CODE = {action.code: action for action in REGISTRY}


def available_for(exc: ExceptionOutcome) -> tuple[ActionOffer, ...]:
    """What this exception can be acted on with, right now.

    The UI renders exactly this. Nothing is hardcoded in the frontend, so an
    action added here appears without a single change there (§8.3).
    """
    return tuple(
        action.offer()  # type: ignore[attr-defined]
        for action in REGISTRY
        if action.is_available(exc)
    )


def action_for(code: str) -> ExceptionAction | None:
    return _BY_CODE.get(code)


def execute(
    code: str, exc: ExceptionOutcome, actor: str, sink: ActionSink
) -> Result[ActionOutcome, str]:
    """Run one action by code. Expected failures are values, not exceptions."""
    action = _BY_CODE.get(code)
    if action is None:
        return Err(f"unknown action {code!r}")
    if not action.is_available(exc):
        return Err(f"{code} is not available for {exc.reason_code}")
    return action.execute(exc, actor, sink)


def undo(
    code: str, outcome: ActionOutcome, actor: str, sink: ActionSink
) -> Result[None, str]:
    action = _BY_CODE.get(code)
    if action is None:
        return Err(f"unknown action {code!r}")
    return action.undo(outcome, actor, sink)
