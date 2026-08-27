"""L5 posting, wired into the run. Guide §4.5, §5.4.

Lives in `pipeline/` rather than `posting/` because it is orchestration: it
reaches into the match context to assemble what each entry needs. `posting/`
itself holds only pure builders and the router, and may import nothing beyond
`core/` — §3.2's dependency rule, which the layering check enforces.

Three invariants hold at the end of every run, and each is asserted rather than
assumed (§9.4):

1. Every entry balances — debits equal credits, checked twice.
2. The books balance in aggregate.
3. **BANK + SUSPENSE equals the bank statement, to the paise.** Every credit is
   accounted for as either explained money or money that arrived and cannot yet
   be explained. Nothing is silently dropped, which is why the suspense figure
   can be read as "how much do I still not understand?"

That third one is why a duplicated UTR and a credit awaiting human review both
post to suspense: they are real lines on the statement, and leaving them out
would make the books disagree with the bank.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.config import Settings
from core.models import CashPosition, Direction, JournalEntry, MatchProposal, Record
from core.reason_codes import ReasonCode
from core.run_result import ReviewItem
from core.trace import Trace
from exceptions_.classifier import (
    LedgerFinding,
    classify_unsettled_ledger_rows,
    in_transit_total,
)
from matching.fee_model import FeeModel
from matching.protocols import MatchContext
from persistence.repositories import InMemoryJournalRepository, JournalRepository
from pipeline.trace import build_explained
from posting.cash_position import compute_cash_position
from posting.confidence_router import Route, route_for
from posting.journal_builder import build_settlement_entry, build_suspense_entry


@dataclass(frozen=True, slots=True)
class PostingResult:
    entries: tuple[JournalEntry, ...]
    review_queue: tuple[ReviewItem, ...]
    cash_position: CashPosition
    duplicates_refused: int
    #: Unsettled ledger rows: in-transit money and never-settling sales.
    findings: tuple[LedgerFinding, ...] = ()
    #: The §8.5 trail behind every match, keyed by UTR. Built here because this
    #: is where the rows, the fee split and the credit are all in hand at once.
    traces: dict[str, Trace] = field(default_factory=dict)
    #: Journal number per posted entry, as the book issued it.
    entry_numbers: dict[str, str] = field(default_factory=dict)


class Poster:
    """Routes every match and every leftover credit into the books."""

    name = "L5_post"

    def __init__(
        self, repository: JournalRepository | None = None, *, settings: Settings
    ) -> None:
        # `or` would be wrong here: the repository defines __len__, so an
        # EMPTY one is falsy and would be silently replaced by a fresh
        # instance — meaning a caller's book was discarded and idempotency
        # across runs never actually held.
        self._repo = (
            repository if repository is not None else InMemoryJournalRepository()
        )
        self._settings = settings
        self._findings: list[LedgerFinding] = []

    def post_all(self, ctx: MatchContext) -> PostingResult:
        fee = ctx.fee_model or FeeModel.disabled()
        review: list[ReviewItem] = []
        posted_utrs: set[str] = set()
        traces: dict[str, Trace] = {}

        for proposal in sorted(ctx.accepted, key=lambda p: p.bank_utr):
            credit = ctx.bank_by_utr.get(proposal.bank_utr, [None])[0]
            if credit is None:
                continue
            entry = self._entry_for(ctx, proposal, credit, fee)
            traces[proposal.bank_utr] = self._trace_for(ctx, proposal, credit, fee)

            match route_for(proposal.confidence, self._settings):
                case Route.AUTO_POST:
                    self._repo.post(entry)
                    posted_utrs.add(proposal.bank_utr)
                case Route.REVIEW:
                    # Prepared, not posted. The money stays in suspense until a
                    # human approves, so the books never claim what nobody
                    # confirmed.
                    review.append(
                        ReviewItem(
                            utr=proposal.bank_utr,
                            ledger_ids=proposal.ledger_ids,
                            confidence=proposal.confidence,
                            reason=proposal.reason,
                            prepared_entry=entry,
                        )
                    )
                case Route.EXCEPTION:
                    pass

        # Every credit not in the books is money that arrived unexplained.
        # Posting it to suspense is what keeps the books tied to the statement.
        seen: dict[str, int] = {}
        for credit in self._bank_rows(ctx):
            seen[credit.external_id] = seen.get(credit.external_id, 0) + 1
            if credit.external_id in posted_utrs:
                posted_utrs.discard(credit.external_id)  # the first copy only
                continue
            copy = seen[credit.external_id]
            suffix = f" (copy {copy} of this UTR)" if copy > 1 else ""
            self._repo.post(
                build_suspense_entry(
                    entry_date=credit.value_date,
                    utr=credit.external_id,
                    paise=credit.amount.paise,
                    occurrence=copy,
                    narration=(
                        f"Unreconciled credit {credit.external_id}"
                        f"{suffix} ({credit.narration or 'no narration'}) "
                        "held in suspense"
                    ),
                )
            )

        if isinstance(self._repo, InMemoryJournalRepository):
            self._repo.assert_books_balance()

        entries = self._repo.all()

        # Sales no credit accounts for. A captured one is money in transit, not
        # an exception — Appendix A is explicit, and conflating them tells a
        # controller their books are broken when they are merely waiting.
        findings = classify_unsettled_ledger_rows(
            ctx.ledger_by_id.values(),
            matched_ids={oid for p in ctx.accepted for oid in p.ledger_ids},
            settled_ids={
                oid
                for rec in ctx.settlements
                for oid in rec.settlement().order_ids
            },
        )
        self._findings = findings

        in_transit = in_transit_total(findings) + sum(
            f.amount_paise or 0
            for f in ctx.flags
            if f.reason_code is ReasonCode.AWAITING_SETTLEMENT
        )
        exception_flags = [
            f for f in ctx.flags if f.reason_code is not ReasonCode.AWAITING_SETTLEMENT
        ]
        exception_findings = [f for f in findings if not f.is_in_transit]
        position = compute_cash_position(
            entries,
            in_transit=in_transit,
            pending_review=len(review),
            pending_review_paise=sum(
                i.prepared_entry.amount_for("1000 Bank Account") for i in review
            ),
            exceptions=len(exception_flags) + len(exception_findings),
            exceptions_paise=(
                sum(f.amount_paise or 0 for f in exception_flags)
                + sum(f.amount_paise for f in exception_findings)
            ),
        )
        refused = getattr(self._repo, "rejected_duplicates", 0)
        numbers = {
            e.idempotency_key: self._repo.number_for(e.idempotency_key) or ""
            for e in entries
        }
        return PostingResult(
            entries,
            tuple(review),
            position,
            refused,
            tuple(findings),
            traces=traces,
            entry_numbers=numbers,
        )

    # ------------------------------------------------------------------

    def _bank_rows(self, ctx: MatchContext) -> list[Record]:
        """Every credit line on the statement, duplicates included.

        Duplicates are NOT collapsed: they are lines the bank reported, so the
        books have to account for them or they cannot tie. A duplicated UTR
        lands in suspense, which is precisely the honest place for money that
        appears to have arrived twice.
        """
        rows = [
            row
            for utr in sorted(ctx.bank_by_utr)
            for row in ctx.bank_by_utr[utr]
            if row.direction is Direction.INFLOW
        ]
        return rows

    def _trace_for(
        self,
        ctx: MatchContext,
        proposal: MatchProposal,
        credit: Record,
        fee: FeeModel,
    ) -> Trace:
        """The §8.5 trail for one match, from the same figures the entry used."""
        rows = [
            ctx.ledger_by_id[oid]
            for oid in sorted(proposal.ledger_ids)
            if oid in ctx.ledger_by_id
        ]
        fee_paise, gst_paise = self._fee_split(ctx, proposal, rows, fee)
        return build_explained(
            proposal, credit, rows, fee, fee_paise=fee_paise, gst_paise=gst_paise
        )

    def _fee_split(
        self,
        ctx: MatchContext,
        proposal: MatchProposal,
        rows: list[Record],
        fee: FeeModel,
    ) -> tuple[int, int]:
        """The gateway's stated fee and GST, or the inferred ones when L3
        explained a credit that has no settlement row to quote.

        Extracted so the entry and the trace cannot disagree about the split —
        two call sites recomputing the same thing is how a diagram ends up
        describing a posting that was never made.
        """
        settlement = (
            ctx.settlement_by_id.get(proposal.settlement_id)
            if proposal.settlement_id
            else None
        )
        if settlement is not None:
            detail = settlement.settlement()
            return detail.fee.paise, detail.gst.paise
        gross_orders = sum(
            r.amount.paise for r in rows if r.direction is Direction.INFLOW
        )
        fee_paise = int(gross_orders * fee.rate)
        return fee_paise, int(fee_paise * fee.gst_rate)

    def _entry_for(
        self,
        ctx: MatchContext,
        proposal: MatchProposal,
        credit: Record,
        fee: FeeModel,
    ) -> JournalEntry:
        """Build the decomposed entry for one match.

        Prefers the fee and GST the settlement report *states* over anything
        recomputed: the gateway's own figures are authoritative, and using them
        leaves nothing for the rounding line to absorb. Only credits L3
        explained — which by definition have no settlement row — fall back to
        the inferred rate.
        """
        rows = [
            ctx.ledger_by_id[oid]
            for oid in sorted(proposal.ledger_ids)
            if oid in ctx.ledger_by_id
        ]
        gross_orders = sum(
            r.amount.paise for r in rows if r.direction is Direction.INFLOW
        )
        refunds = sum(
            r.amount.paise for r in rows if r.direction is Direction.OUTFLOW
        )

        fee_paise, gst_paise = self._fee_split(ctx, proposal, rows, fee)

        return build_settlement_entry(
            entry_date=credit.value_date,
            utr=proposal.bank_utr,
            gross_orders_paise=gross_orders,
            refunds_paise=refunds,
            fee_paise=fee_paise,
            gst_paise=gst_paise,
            actual_credit_paise=credit.amount.paise,
            narration=proposal.reason,
            ledger_ids=proposal.ledger_ids,
            settlement_id=proposal.settlement_id,
            confidence=proposal.confidence,
            strategy=proposal.strategy,
        )


def bank_statement_total(ctx: MatchContext) -> int:
    """Sum of every credit line on the statement — the tie-out target (§4.5)."""
    return sum(
        row.amount.paise
        for rows in ctx.bank_by_utr.values()
        for row in rows
        if row.direction is Direction.INFLOW
    )


