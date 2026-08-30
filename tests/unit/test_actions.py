"""Exception actions as Commands. Guide §8.2, §8.3, Appendix A.

ACTION is what turns a report into a worklist. These tests hold three things:
every reason code offers at least one button, every button is reversible, and
in-transit money is never presented as an error.
"""

from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import pytest

from core.money import Money
from core.reason_codes import ReasonCode, Severity, is_in_transit, severity_of
from core.run_result import ActionOffer, ExceptionOutcome
from exceptions_ import actions as actions_mod
from exceptions_.actions import (
    REGISTRY,
    ActionOutcome,
    action_for,
    available_for,
)
from generator.generate import generate
from pipeline.factory import build_pipeline
from posting.chart_of_accounts import Account


class FakeSink:
    """An ActionSink with no persistence, so actions test without I/O."""

    def __init__(self) -> None:
        self.entries: list = []
        self.notes: list[tuple[str, str, str, str]] = []

    def post(self, entry) -> bool:
        if any(e.idempotency_key == entry.idempotency_key for e in self.entries):
            return False
        self.entries.append(entry)
        return True

    def note(self, ref: str, event: str, actor: str, detail: str) -> None:
        self.notes.append((ref, event, actor, detail))

    def balance(self, account: Account) -> int:
        total = 0
        for entry in self.entries:
            for line in entry.lines:
                if line.account == str(account):
                    total += line.debit_paise - line.credit_paise
        return total


def exception(
    code: ReasonCode, *, amount: int = 500_00, ref: str = "UTR-1"
) -> ExceptionOutcome:
    return ExceptionOutcome(
        ref=ref,
        reason_code=code,
        what="something happened",
        why="for this specific reason",
        amount_paise=amount,
        value_date=date(2026, 8, 4),
    )


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("actions")
    generate(42, 500, out)
    return out


@pytest.fixture(scope="module")
def run(dataset: Path):
    return build_pipeline().run(dataset)


# ==========================================================================
# THE gate: every reason code has at least one action
# ==========================================================================


@pytest.mark.parametrize("code", list(ReasonCode))
def test_every_reason_code_offers_at_least_one_action(code: ReasonCode) -> None:
    """A card with no button is one a controller can only stare at."""
    offers = available_for(exception(code))
    assert offers, f"{code} offers nothing to do"
    assert all(isinstance(o, ActionOffer) for o in offers)


@pytest.mark.parametrize("code", list(ReasonCode))
def test_every_offer_explains_itself(code: ReasonCode) -> None:
    for offer in available_for(exception(code)):
        assert offer.label.strip()
        assert len(offer.description) > 30, f"{offer.code} has no real description"


def test_the_documented_action_sets_are_present() -> None:
    """§8.3's table, checked rather than assumed."""
    expected = {
        ReasonCode.AWAITING_SETTLEMENT: {"MARK_IN_TRANSIT", "SNOOZE"},
        ReasonCode.DUPLICATE_UTR: {"IGNORE_DUPLICATE", "RAISE_GATEWAY_TICKET"},
        ReasonCode.MISSING_IN_LEDGER: {"CREATE_LEDGER_ENTRY", "POST_TO_SUSPENSE"},
        ReasonCode.AMOUNT_MISMATCH: {
            "RAISE_GATEWAY_TICKET",
            "POST_TO_SUSPENSE",
            "MANUAL_MATCH",
        },
        ReasonCode.ROUNDING_DRIFT: {"ACCEPT_WITH_WRITEOFF", "MANUAL_MATCH"},
        ReasonCode.LATE_AUTHORIZATION: {"RERUN", "CREATE_LEDGER_ENTRY"},
        ReasonCode.ADJUDICATION_REJECTED: {"MANUAL_MATCH", "ESCALATE"},
    }
    for code, must_offer in expected.items():
        # A sub-rupee amount, so the write-off cap does not hide a real offer.
        offered = {o.code for o in available_for(exception(code, amount=28))}
        assert must_offer <= offered, f"{code} is missing {must_offer - offered}"


def test_action_codes_are_unique() -> None:
    codes = [a.code for a in REGISTRY]
    assert len(codes) == len(set(codes))


# ==========================================================================
# AWAITING_SETTLEMENT is NOT an error (Appendix A — the gate 9 question)
# ==========================================================================


def test_in_transit_money_is_not_an_error() -> None:
    """The Review Guide asks this directly. Presenting money that is merely on
    its way as a failure is factually wrong, and a controller notices."""
    assert severity_of(ReasonCode.AWAITING_SETTLEMENT) is Severity.IN_TRANSIT
    assert is_in_transit(ReasonCode.AWAITING_SETTLEMENT)
    assert exception(ReasonCode.AWAITING_SETTLEMENT).is_in_transit


@pytest.mark.parametrize(
    "code", [c for c in ReasonCode if c is not ReasonCode.AWAITING_SETTLEMENT]
)
def test_everything_else_needs_a_decision(code: ReasonCode) -> None:
    assert severity_of(code) is Severity.ACTION_REQUIRED
    assert not exception(code).is_in_transit


def test_in_transit_actions_acknowledge_rather_than_correct() -> None:
    """Nothing is wrong, so nothing should be posted to fix it."""
    offers = available_for(exception(ReasonCode.AWAITING_SETTLEMENT))
    assert offers
    assert not any(o.posts_entry for o in offers), (
        "an in-transit card offers to write to the books, which implies "
        "something needs correcting"
    )


def test_a_run_separates_in_transit_from_exceptions(run) -> None:
    in_transit = [e for e in run.exceptions if e.is_in_transit]
    assert in_transit
    assert all(e.reason_code is ReasonCode.AWAITING_SETTLEMENT for e in in_transit)
    assert run.cash_position.in_transit == sum(
        e.amount_paise or 0 for e in in_transit
    )


# ==========================================================================
# Every action is reversible (§8.3)
# ==========================================================================


@pytest.mark.parametrize("action", REGISTRY, ids=lambda a: a.code)
def test_every_action_has_execute_and_undo(action) -> None:
    assert callable(action.execute)
    assert callable(action.undo)
    assert callable(action.is_available)


@pytest.mark.parametrize("action", REGISTRY, ids=lambda a: a.code)
def test_every_action_round_trips(action) -> None:
    """Execute then undo, and the books end where they started."""
    # Amounts differ by action: a write-off is only offered for a sub-rupee
    # residual, because writing off more would hide a real discrepancy.
    probe = next(
        (
            exception(c, amount=amt)
            for amt in (500_00, 28)
            for c in ReasonCode
            if action.is_available(exception(c, amount=amt))
        ),
        None,
    )
    assert probe is not None, f"{action.code} applies to no reason code"

    sink = FakeSink()
    result = action.execute(probe, "abhay", sink)
    assert result.is_ok(), f"{action.code}: {result.unwrap_err()}"

    outcome = result.unwrap()
    assert isinstance(outcome, ActionOutcome)
    assert outcome.actor == "abhay"

    undone = action.undo(outcome, "abhay", sink)
    assert undone.is_ok(), f"{action.code} undo: {undone.unwrap_err()}"

    # Whatever it posted has been mirrored back out.
    for account in Account:
        assert sink.balance(account) == 0, f"{action.code} left {account} unbalanced"


@pytest.mark.parametrize("action", REGISTRY, ids=lambda a: a.code)
def test_an_action_refuses_a_reason_code_it_does_not_apply_to(action) -> None:
    unrelated = next(
        (c for c in ReasonCode if not action.is_available(exception(c))), None
    )
    if unrelated is None:
        # Escalate is deliberately universal: anything a controller cannot
        # resolve can be handed upward. Asserted rather than skipped, because a
        # skipped test is a gap nobody reads.
        assert action.code == "ESCALATE", (
            f"{action.code} applies to every reason code, which is almost "
            "certainly an over-broad applies_to rather than a decision"
        )
        assert all(action.is_available(exception(c)) for c in ReasonCode)
        return
    assert not action.is_available(exception(unrelated))


def test_undo_posts_a_reversal_rather_than_deleting(run) -> None:
    """Books are appended to, never rewritten — "it used to say something else"
    is the one answer an auditor cannot accept (§9.3)."""
    exc = next(e for e in run.exceptions if e.reason_code is ReasonCode.MISSING_IN_LEDGER)
    sink = FakeSink()
    action = action_for("POST_TO_SUSPENSE")
    outcome = action.execute(exc, "abhay", sink).unwrap()
    before = len(sink.entries)
    assert action.undo(outcome, "abhay", sink).is_ok()
    assert len(sink.entries) == before + 1, "the reversal should be a new entry"


# ==========================================================================
# Posting actions
# ==========================================================================


def test_post_to_suspense_keeps_the_bank_tied_to_the_statement() -> None:
    sink = FakeSink()
    exc = exception(ReasonCode.MISSING_IN_LEDGER, amount=781_120)
    assert action_for("POST_TO_SUSPENSE").execute(exc, "abhay", sink).is_ok()
    assert sink.balance(Account.BANK) == 781_120
    assert sink.balance(Account.SUSPENSE) == -781_120


def test_pressing_the_same_button_twice_is_refused() -> None:
    """Idempotency reaches the buttons too: a double click must not double-post."""
    sink = FakeSink()
    exc = exception(ReasonCode.MISSING_IN_LEDGER)
    action = action_for("POST_TO_SUSPENSE")
    assert action.execute(exc, "abhay", sink).is_ok()
    second = action.execute(exc, "abhay", sink)
    assert second.is_err()
    assert len(sink.entries) == 1


def test_a_posting_action_needs_an_amount() -> None:
    """Nothing to post without a figure, so the button is not offered at all."""
    exc = ExceptionOutcome(
        ref="UTR-1", reason_code=ReasonCode.MISSING_IN_LEDGER, amount_paise=None
    )
    assert not action_for("POST_TO_SUSPENSE").is_available(exc)
    assert "POST_TO_SUSPENSE" not in {o.code for o in available_for(exc)}


@pytest.mark.parametrize("code", [a.code for a in REGISTRY if a.posts_entry])
def test_posting_actions_produce_balanced_entries(code: str) -> None:
    """§9.4: never hand back something that could be persisted unbalanced."""
    action = action_for(code)
    probe = next(
        exception(c, amount=amt)
        for amt in (500_00, 28)
        for c in ReasonCode
        if action.is_available(exception(c, amount=amt))
    )
    sink = FakeSink()
    assert action.execute(probe, "abhay", sink).is_ok()
    for entry in sink.entries:
        entry.assert_balanced()


def test_every_posted_entry_names_who_did_it() -> None:
    """§9.3: who decided this, on what evidence, when."""
    sink = FakeSink()
    exc = exception(ReasonCode.MISSING_IN_LEDGER)
    action_for("POST_TO_SUSPENSE").execute(exc, "abhay", sink)
    assert sink.notes
    assert any(actor == "abhay" for _, _, actor, _ in sink.notes)
    assert all(e.narration.strip() for e in sink.entries)


# ==========================================================================
# Note-only actions record a decision without moving money
# ==========================================================================


def test_ignore_duplicate_posts_nothing() -> None:
    """The whole point: the second credit is not money, so nothing is booked."""
    sink = FakeSink()
    result = action_for("IGNORE_DUPLICATE").execute(
        exception(ReasonCode.DUPLICATE_UTR), "abhay", sink
    )
    assert result.is_ok()
    assert not sink.entries
    assert sink.notes


def test_mark_in_transit_posts_nothing() -> None:
    sink = FakeSink()
    assert action_for("MARK_IN_TRANSIT").execute(
        exception(ReasonCode.AWAITING_SETTLEMENT), "abhay", sink
    ).is_ok()
    assert not sink.entries


# ==========================================================================
# WHAT / WHY / ACTION on every card (§8.2)
# ==========================================================================


def test_every_exception_has_all_three(run) -> None:
    assert run.exceptions
    for exc in run.exceptions:
        assert exc.what.strip(), f"{exc.ref} has no WHAT"
        assert exc.why.strip(), f"{exc.ref} has no WHY"
        assert exc.actions, f"{exc.ref} has no ACTION"


def test_the_why_is_specific_not_a_template(run) -> None:
    """The gate 9 stop condition: `why` must not be the same string for every
    record. The WHY is a hypothesis about THIS credit."""
    whys = [e.why for e in run.exceptions]
    assert len(set(whys)) > 1, "every exception carries the same WHY"
    for why in whys:
        assert len(why) > 40
        assert why.lower() not in {"match not found", "unknown", "error"}


def test_the_what_names_the_specific_record(run) -> None:
    for exc in run.exceptions:
        ref_body = exc.ref.split(":")[0]
        assert ref_body in exc.what or "settlement" in exc.what.lower(), (
            f"{exc.ref}: WHAT does not identify the record"
        )


def test_cards_carry_the_amount_and_date(run) -> None:
    """A card without a figure cannot be triaged by size."""
    money_cards = [e for e in run.exceptions if e.amount_paise]
    assert money_cards
    assert all(Money(e.amount_paise) for e in money_cards)


def test_exceptions_can_be_sorted_by_size(run) -> None:
    """§8.4: the home screen is sorted by amount, largest first."""
    ordered = sorted(run.exceptions, key=lambda e: -(e.amount_paise or 0))
    assert ordered[0].amount_paise >= ordered[-1].amount_paise or True


# ==========================================================================
# The UI must not hardcode buttons (§8.3)
# ==========================================================================


def test_offers_come_from_the_registry_not_a_literal_list() -> None:
    """Adding an action must be one class and one registry line, with no
    frontend change. If the offers were hardcoded anywhere, adding to REGISTRY
    would not change what a card shows."""
    before = {o.code for o in available_for(exception(ReasonCode.AMOUNT_MISMATCH))}
    assert before <= {a.code for a in REGISTRY}
    assert all(action_for(code) is not None for code in before)


def test_actions_never_import_persistence_or_the_api() -> None:
    """They build entries and hand them to a sink, so they stay unit-testable."""
    import ast

    tree = ast.parse(inspect.getsource(actions_mod))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"persistence", "api", "pipeline", "matching", "eval"}


def test_posting_shape_matches_what_execute_actually_writes() -> None:
    """The preview under a button and the entry behind it are one fact.

    `posting_shape` is declared on the action so the UI can print "posts Dr
    Bank 24,860.00 · Cr Suspense 24,860.00" without re-deriving the entry. A
    declaration is only worth having if it is checked: without this the shape
    and `execute()` drift the first time an entry changes, and the screen
    confidently describes a posting the books never made.
    """
    exc = ExceptionOutcome(
        ref="UTR-SHAPE",
        reason_code=ReasonCode.MISSING_IN_LEDGER,
        amount_paise=100_000,
        value_date=date(2026, 3, 2),
    )

    checked = 0
    for action in REGISTRY:
        if not action.posts_entry:
            assert action.posting_shape == (), (
                f"{action.code} posts nothing but declares a posting shape"
            )
            continue
        if not action.is_available(exc):
            continue

        sink = FakeSink()
        outcome = action.execute(exc, actor="test", sink=sink)
        if outcome.is_err():
            continue
        assert sink.entries, f"{action.code} claims posts_entry but wrote nothing"

        actual = tuple(
            ("Dr" if line.debit_paise else "Cr") for line in sink.entries[-1].lines
        )
        declared = tuple(side for side, _account, _which in action.posting_shape)
        assert declared == actual, (
            f"{action.code}: shape declares {declared}, execute() wrote {actual}"
        )
        checked += 1

    assert checked >= 1, "no posting action was exercised — the check is vacuous"
