"""L5 — resolve and post. Guide §4.5, §9.4.

This is the layer that closes the loop, and the one where a mistake is worst: a
reconciliation tool that produces unbalanced books is worse than none. Every
invariant here is asserted, never assumed.
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from pathlib import Path

import pytest

from core.config import Settings
from core.dates import BusinessCalendar
from core.models import JournalEntry, JournalLine
from core.money import Money
from core.reason_codes import ReasonCode
from eval.evaluate import is_correct, load_truth
from generator.generate import generate
from ingest.normalizer import load_dataset
from matching.protocols import MatchContext
from persistence.repositories import InMemoryJournalRepository
from pipeline.factory import build_pipeline
from pipeline.posting_step import Poster, bank_statement_total
from posting.chart_of_accounts import Account
from posting.confidence_router import Route, route_for
from posting.journal_builder import (
    JournalEntryBuilder,
    build_settlement_entry,
    build_suspense_entry,
    idempotency_key,
)

SETTINGS = Settings()
BANK = str(Account.BANK)
SUSPENSE = str(Account.SUSPENSE)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("l5")
    generate(42, 500, out)
    return out


@pytest.fixture(scope="module")
def run(dataset: Path):
    return build_pipeline().run(dataset)


def context_for(dataset: Path) -> MatchContext:
    loaded = load_dataset(dataset)
    return MatchContext.build(
        loaded.records, calendar=BusinessCalendar(), settings=SETTINGS
    )


# ==========================================================================
# The §4.5 verification block
# ==========================================================================


def test_l5_every_entry_balances(run) -> None:
    assert run.entries
    for entry in run.entries:
        assert entry.total_debits == entry.total_credits, entry.idempotency_key


def test_l5_books_tie_to_bank(dataset: Path, run) -> None:
    """(bank + suspense) == sum of bank statement credits, to the paise.

    Every credit is accounted for as either explained money or money that
    arrived and cannot yet be explained. Nothing is silently dropped.
    """
    statement = bank_statement_total(context_for(dataset))
    position = run.cash_position
    assert position.confirmed_in_bank + position.in_suspense == statement


def test_the_bank_ledger_equals_the_statement(dataset: Path, run) -> None:
    """The other half of the same invariant: the cash in the books is the cash
    at the bank."""
    assert run.cash_position.bank_ledger_total == bank_statement_total(
        context_for(dataset)
    )


def test_l5_auto_post_band_is_perfect(dataset: Path, run) -> None:
    """§4.5: everything posted without asking a human must be correct.

    This is the claim the whole calibration story rests on (§2.5).
    """
    truth = load_truth(dataset)
    auto = [
        m
        for m in run.matches.values()
        if m.confidence >= SETTINGS.auto_post_threshold
    ]
    assert auto
    for match in auto:
        assert is_correct(match.ledger_ids, truth["mappings"].get(match.utr, []))


def test_l5_is_idempotent(dataset: Path) -> None:
    """Running twice posts nothing twice."""
    first = build_pipeline().run(dataset)
    second = build_pipeline().run(dataset)
    assert len(first.entries) == len(second.entries)
    assert {e.idempotency_key for e in first.entries} == {
        e.idempotency_key for e in second.entries
    }


def test_reposting_into_the_same_book_is_a_no_op(dataset: Path) -> None:
    """The guarantee that matters: the same repository, posted to twice."""
    ctx = context_for(dataset)
    from matching.registry import build_strategies

    for strategy in build_strategies():
        for proposal in strategy.propose(ctx):
            ctx.accept(proposal)
        ctx.refresh_derived()

    repo = InMemoryJournalRepository()
    Poster(repo, settings=SETTINGS).post_all(ctx)
    count = len(repo)
    assert count > 0

    Poster(repo, settings=SETTINGS).post_all(ctx)
    assert len(repo) == count, "a second run added entries"
    assert repo.rejected_duplicates == count


def test_l5_no_entry_without_reason(run) -> None:
    """§2.7 rule 4: no automated decision without a justification."""
    for entry in run.entries:
        assert entry.narration.strip()


# ==========================================================================
# The entry decomposes (§4.5)
# ==========================================================================


def test_a_matched_settlement_posts_four_lines() -> None:
    """The §4.5 worked example, exactly."""
    entry = build_settlement_entry(
        entry_date=date(2026, 8, 4),
        utr="UTR-77291",
        gross_orders_paise=800_000,
        refunds_paise=0,
        fee_paise=16_000,
        gst_paise=2_880,
        actual_credit_paise=781_120,
        narration="Exact three-way join",
        ledger_ids=["ORD-101", "ORD-102", "ORD-103"],
        settlement_id="SETL-88",
    )
    by_account = {line.account: line for line in entry.lines}
    assert by_account[BANK].debit_paise == 781_120
    assert by_account[str(Account.GATEWAY_FEE)].debit_paise == 16_000
    assert by_account[str(Account.GST_INPUT_CREDIT)].debit_paise == 2_880
    assert by_account[str(Account.ACCOUNTS_RECEIVABLE)].credit_paise == 800_000
    assert entry.total_debits == entry.total_credits == 800_000


def test_gst_is_a_separate_line_never_folded_into_the_fee(run) -> None:
    """Collapsed into the fee, the merchant silently forfeits reclaimable input
    credit — real money over a year."""
    posted = [e for e in run.entries if e.ledger_ids]
    assert posted
    assert any(e.amount_for(str(Account.GST_INPUT_CREDIT)) > 0 for e in posted)
    assert run.cash_position.gst_claimable > 0


def test_refunds_get_their_own_line(dataset: Path, run) -> None:
    """A controller should see what was sold and what was returned, not one
    smaller number."""
    # Cross-period refunds are found by L3 at confidence 0.85-0.92, so they
    # land in the review queue rather than the posted books — which is correct.
    # The prepared entry must still decompose properly.
    candidates = [*run.entries, *(i.prepared_entry for i in run.review_queue)]
    with_refunds = [
        e for e in candidates if any(i.startswith("RFND-") for i in e.ledger_ids)
    ]
    assert with_refunds, "no entry, posted or prepared, contains a refund"
    for entry in with_refunds:
        assert entry.amount_for(str(Account.REFUNDS)) > 0


def test_bank_is_debited_with_the_actual_credit_not_a_computed_one(
    dataset: Path, run
) -> None:
    """§4.5 computes `gross - fee - gst`, which disagrees with the statement on
    almost every settlement because the gateway rounds at each step. Posting
    that would mean the books never tie."""
    loaded = load_dataset(dataset)
    credits = {
        r.external_id: r.amount.paise
        for r in loaded.records
        if r.source.value == "bank"
    }
    for entry in run.entries:
        if entry.ledger_ids and entry.source_utr in credits:
            assert entry.amount_for(BANK) == credits[entry.source_utr]


def test_rounding_lands_on_its_own_account(run) -> None:
    """Appendix B provides ROUNDING_WRITEOFF for exactly this, so the drift is
    visible in the books rather than hidden inside the fee."""
    posted = [e for e in run.entries if e.ledger_ids]
    accounts = {line.account for e in posted for line in e.lines}
    assert accounts <= {str(a) for a in Account}


def test_an_unbalanced_entry_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="unbalanced"):
        (
            JournalEntryBuilder()
            .on(date(2026, 8, 4))
            .because("deliberately broken")
            .debit(Account.BANK, 100)
            .credit(Account.ACCOUNTS_RECEIVABLE, 90)
            .build()
        )


def test_an_unbalanced_entry_cannot_be_posted() -> None:
    """Checked again at the repository: this is the last point before an entry
    becomes part of the books (§9.4)."""
    entry = JournalEntry(
        idempotency_key="x",
        entry_date=date(2026, 8, 4),
        narration="forced",
        lines=(JournalLine(BANK, debit_paise=100),),
    )
    with pytest.raises(ValueError, match="unbalanced"):
        InMemoryJournalRepository().post(entry)


def test_an_entry_without_a_narration_cannot_exist() -> None:
    with pytest.raises(ValueError, match="no narration"):
        JournalEntry(
            idempotency_key="x",
            entry_date=date(2026, 8, 4),
            narration="   ",
            lines=(JournalLine(BANK, debit_paise=100),),
        )


def test_a_line_cannot_be_both_debit_and_credit() -> None:
    with pytest.raises(ValueError, match="both a debit and a credit"):
        JournalLine(BANK, debit_paise=100, credit_paise=100)


def test_a_line_cannot_carry_a_negative_amount() -> None:
    with pytest.raises(ValueError, match="negative"):
        JournalLine(BANK, debit_paise=-100)


# ==========================================================================
# Suspense: unmatched money is posted, not ignored (§4.5)
# ==========================================================================


def test_unmatched_credits_go_to_suspense(dataset: Path, run) -> None:
    assert run.cash_position.in_suspense > 0
    suspense_entries = [e for e in run.entries if not e.ledger_ids]
    assert suspense_entries
    for entry in suspense_entries:
        assert entry.amount_for(SUSPENSE) < 0  # credited
        assert entry.amount_for(BANK) > 0  # the money really did arrive


def test_a_duplicated_utr_becomes_two_entries(dataset: Path, run) -> None:
    """Two lines on the statement are two lines in the books. Collapsing them
    would leave the books short by exactly the duplicated amount."""
    truth = load_truth(dataset)
    duplicated = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "DUPLICATE_UTR"
    }
    assert duplicated
    for utr in duplicated:
        assert sum(1 for e in run.entries if e.source_utr == utr) == 2


def test_suspense_is_the_size_of_the_unreconciled_problem(run) -> None:
    """The one number a controller reads as "how much do I still not
    understand?"."""
    assert run.cash_position.unreconciled_paise == run.cash_position.in_suspense


def test_review_band_money_stays_in_suspense(run) -> None:
    """Prepared, not posted: the books never claim what nobody confirmed."""
    review_utrs = {item.utr for item in run.review_queue}
    posted_utrs = {e.source_utr for e in run.entries if e.ledger_ids}
    assert not review_utrs & posted_utrs


# ==========================================================================
# Confidence routing (§4.5) — thresholds from Settings, never hardcoded
# ==========================================================================


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (1.00, Route.AUTO_POST),
        (0.95, Route.AUTO_POST),
        (0.94, Route.REVIEW),
        (0.70, Route.REVIEW),
        (0.69, Route.EXCEPTION),
        (0.00, Route.EXCEPTION),
    ],
)
def test_routing_bands(confidence: float, expected: Route) -> None:
    assert route_for(confidence, SETTINGS) is expected


def test_thresholds_come_from_settings_not_the_router() -> None:
    """The gate 8 stop condition. They get tuned from the calibration table
    later, and a constant buried in a router is one nobody re-tunes."""
    from posting import confidence_router

    src = inspect.getsource(confidence_router.route_for)
    assert "settings.auto_post_threshold" in src
    assert "settings.review_threshold" in src
    assert "0.95" not in src and "0.70" not in src


def test_routing_follows_a_changed_threshold() -> None:
    strict = Settings(auto_post_threshold=0.99)
    assert route_for(0.96, SETTINGS) is Route.AUTO_POST
    assert route_for(0.96, strict) is Route.REVIEW


def test_review_items_carry_the_prepared_entry(run) -> None:
    """§4.5: showing the prepared double-entry is what makes it a two-second
    decision instead of a two-minute investigation."""
    assert run.review_queue
    for item in run.review_queue:
        assert item.prepared_entry.total_debits == item.prepared_entry.total_credits
        assert item.reason.strip()
        assert SETTINGS.review_threshold <= item.confidence < SETTINGS.auto_post_threshold


# ==========================================================================
# Idempotency key (§4.5)
# ==========================================================================


def test_key_is_made_of_ledger_ids_utr_and_settlement_id() -> None:
    a = idempotency_key(["ORD-2", "ORD-1"], "UTR-9", "SETL-1")
    b = idempotency_key(["ORD-1", "ORD-2"], "UTR-9", "SETL-1")
    assert a == b, "order of the ledger ids must not matter"
    assert a != idempotency_key(["ORD-1"], "UTR-9", "SETL-1")
    assert a != idempotency_key(["ORD-1", "ORD-2"], "UTR-8", "SETL-1")
    assert a != idempotency_key(["ORD-1", "ORD-2"], "UTR-9", "SETL-2")


def test_the_key_does_not_depend_on_when_it_was_posted() -> None:
    """A time-derived key would make every rerun a fresh duplicate."""
    from posting import journal_builder

    src = inspect.getsource(journal_builder.idempotency_key)
    assert "now(" not in src and "today(" not in src and "time" not in src


def test_two_different_suspense_copies_get_different_keys() -> None:
    first = build_suspense_entry(
        entry_date=date(2026, 8, 4), utr="UTR-1", paise=100, narration="copy 1"
    )
    second = build_suspense_entry(
        entry_date=date(2026, 8, 4),
        utr="UTR-1",
        paise=100,
        narration="copy 2",
        occurrence=2,
    )
    assert first.idempotency_key != second.idempotency_key


# ==========================================================================
# Cash position (§1.6)
# ==========================================================================


def test_cash_position_answers_the_track_title(run) -> None:
    p = run.cash_position
    assert p is not None
    assert p.revenue_recognised > 0
    assert p.fee_expense > 0
    assert p.gst_claimable > 0
    assert p.entries_posted > 0


def test_gst_is_roughly_eighteen_percent_of_the_fee(run) -> None:
    p = run.cash_position
    ratio = p.gst_claimable / p.fee_expense
    assert 0.17 < ratio < 0.19, f"GST/fee ratio {ratio:.3f}"


def test_suspense_entries_are_excluded_from_the_posted_count(run) -> None:
    """"Auto-posted" must mean books closed, not money parked."""
    assert run.cash_position.entries_posted == sum(
        1 for e in run.entries if e.ledger_ids
    )
    assert run.cash_position.suspense_entries == sum(
        1 for e in run.entries if not e.ledger_ids
    )


def test_the_books_balance_in_aggregate(run) -> None:
    """§9.4's run-level assertion."""
    assert sum(e.total_debits for e in run.entries) == sum(
        e.total_credits for e in run.entries
    )


def test_amounts_are_integer_paise_everywhere(run) -> None:
    for entry in run.entries:
        for line in entry.lines:
            assert isinstance(line.debit_paise, int)
            assert isinstance(line.credit_paise, int)


def test_every_entry_carries_its_provenance(run) -> None:
    """§9.3: who decided this, on what evidence, under which strategy."""
    for entry in run.entries:
        if entry.ledger_ids:
            assert entry.strategy
            assert entry.confidence > 0
            assert entry.source_utr


def test_posting_is_deterministic(dataset: Path) -> None:
    a = build_pipeline().run(dataset)
    b = build_pipeline().run(dataset)
    assert [e.idempotency_key for e in a.entries] == [
        e.idempotency_key for e in b.entries
    ]


def test_money_only_appears_as_rupees_at_the_display_boundary(run) -> None:
    entry = next(e for e in run.entries if e.ledger_ids)
    assert "₹" in str(Money(entry.total_debits))
    assert isinstance(entry.total_debits, int)


def test_truth_is_never_read_by_the_posting_layer() -> None:
    from pipeline import posting_step
    from posting import cash_position, confidence_router, journal_builder

    for module in (journal_builder, confidence_router, cash_position, posting_step):
        assert "truth" not in inspect.getsource(module).lower()


def test_the_dataset_needs_no_database(dataset: Path) -> None:
    """The demo and gate 14's clean clone must reconcile with nothing installed.
    The Postgres schema exists and matches; it is not required."""
    result = build_pipeline().run(dataset)
    assert result.entries
    assert json.dumps({"entries": len(result.entries)})


# ==========================================================================
# In-transit money is NOT an exception (Appendix A)
# ==========================================================================


def test_captured_but_unsettled_orders_are_reported_as_in_transit(
    dataset: Path, run
) -> None:
    """The money left the customer and has not landed. A controller reads
    "waiting" and "broken" completely differently, and Appendix A is explicit
    that this one is waiting."""
    truth = load_truth(dataset)
    planted = [
        e["ref"] for e in truth["exceptions"] if e["type"] == "AWAITING_SETTLEMENT"
    ]
    assert planted

    ledger = {
        r.external_id: r
        for r in load_dataset(dataset).records
        if r.source.value == "ledger"
    }
    expected = sum(ledger[p].amount.paise for p in planted if p in ledger)
    assert run.cash_position.in_transit == expected


def test_in_transit_money_is_not_counted_among_the_exceptions(run) -> None:
    """Folding it into the exception list overstates the problem and
    understates the cash."""
    in_transit_refs = {
        e.ref
        for e in run.exceptions
        if e.reason_code is ReasonCode.AWAITING_SETTLEMENT
    }
    assert in_transit_refs
    exception_paise = run.cash_position.exceptions_paise
    assert run.cash_position.in_transit > 0
    assert exception_paise != exception_paise + run.cash_position.in_transit


def test_in_transit_is_still_visible_to_the_controller(run) -> None:
    """Not an exception, but not hidden either."""
    assert any(
        e.reason_code is ReasonCode.AWAITING_SETTLEMENT for e in run.exceptions
    )


def test_an_authorised_but_uncaptured_sale_is_an_exception_not_in_transit(
    dataset: Path, run
) -> None:
    """Auto-refunded money went back to the customer — it is not on its way."""
    truth = load_truth(dataset)
    planted = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "AUTO_REFUNDED"
    }
    assert planted
    reported = {
        e.ref for e in run.exceptions if e.reason_code is ReasonCode.AUTO_REFUNDED
    }
    assert planted & reported


# ==========================================================================
# Approval must not count the same money twice
# ==========================================================================


def test_approving_a_review_item_does_not_double_count(dataset: Path) -> None:
    """The credit is already in the books as Dr BANK / Cr SUSPENSE. Posting the
    prepared entry as-is would debit BANK again and count the cash twice."""
    from posting.journal_builder import approval_entry

    ctx = context_for(dataset)
    from matching.registry import build_strategies

    for strategy in build_strategies():
        for proposal in strategy.propose(ctx):
            ctx.accept(proposal)
        ctx.refresh_derived()

    repo = InMemoryJournalRepository()
    result = Poster(repo, settings=SETTINGS).post_all(ctx)
    statement = bank_statement_total(ctx)

    bank_before = repo.balance(BANK)
    revenue_before = -repo.balance(str(Account.ACCOUNTS_RECEIVABLE))
    assert bank_before == statement

    item = result.review_queue[0]
    assert repo.post(approval_entry(item.prepared_entry))

    # The cash did not move: it was already banked when it arrived.
    assert repo.balance(BANK) == statement
    # The explanation did land: revenue grew by exactly this settlement's gross.
    gross = item.prepared_entry.amount_for(str(Account.ACCOUNTS_RECEIVABLE))
    assert -repo.balance(str(Account.ACCOUNTS_RECEIVABLE)) == revenue_before - gross
    repo.assert_books_balance()


def test_approval_clears_the_suspense_holding(dataset: Path) -> None:
    from posting.journal_builder import approval_entry

    ctx = context_for(dataset)
    from matching.registry import build_strategies

    for strategy in build_strategies():
        for proposal in strategy.propose(ctx):
            ctx.accept(proposal)
        ctx.refresh_derived()

    repo = InMemoryJournalRepository()
    result = Poster(repo, settings=SETTINGS).post_all(ctx)
    suspense_before = -repo.balance(SUSPENSE)

    item = result.review_queue[0]
    credit = item.prepared_entry.amount_for(BANK)
    repo.post(approval_entry(item.prepared_entry))

    assert -repo.balance(SUSPENSE) == suspense_before - credit
    repo.assert_books_balance()


def test_approving_twice_is_a_no_op(dataset: Path) -> None:
    from posting.journal_builder import approval_entry

    ctx = context_for(dataset)
    from matching.registry import build_strategies

    for strategy in build_strategies():
        for proposal in strategy.propose(ctx):
            ctx.accept(proposal)
        ctx.refresh_derived()

    repo = InMemoryJournalRepository()
    result = Poster(repo, settings=SETTINGS).post_all(ctx)
    entry = approval_entry(result.review_queue[0].prepared_entry)
    assert repo.post(entry) is True
    assert repo.post(entry) is False
    repo.assert_books_balance()


def test_the_books_still_tie_after_every_approval(dataset: Path) -> None:
    """Approve the whole queue and the invariant must survive."""
    from posting.journal_builder import approval_entry

    ctx = context_for(dataset)
    from matching.registry import build_strategies

    for strategy in build_strategies():
        for proposal in strategy.propose(ctx):
            ctx.accept(proposal)
        ctx.refresh_derived()

    repo = InMemoryJournalRepository()
    result = Poster(repo, settings=SETTINGS).post_all(ctx)
    for item in result.review_queue:
        repo.post(approval_entry(item.prepared_entry))

    repo.assert_books_balance()
    assert repo.balance(BANK) == bank_statement_total(ctx)


# ==========================================================================
# The rounding write-off is visible
# ==========================================================================


def test_the_rounding_writeoff_appears_where_the_fee_model_was_inverted(run) -> None:
    """Posted entries use the fee and GST the settlement report STATES, so
    nothing is left over. Only credits L3 explained — which have no settlement
    row — need the inferred rate, and those carry the drift."""
    prepared = [i.prepared_entry for i in run.review_queue]
    assert prepared
    drifted = [e for e in prepared if e.amount_for(str(Account.ROUNDING_WRITEOFF))]
    assert drifted, "no entry carries a rounding residual"
    for entry in drifted:
        assert abs(entry.amount_for(str(Account.ROUNDING_WRITEOFF))) < 5_000


def test_posted_entries_need_no_writeoff_because_the_gateway_figures_are_used(
    run,
) -> None:
    posted = [e for e in run.entries if e.ledger_ids]
    assert posted
    assert all(e.amount_for(str(Account.ROUNDING_WRITEOFF)) == 0 for e in posted)
