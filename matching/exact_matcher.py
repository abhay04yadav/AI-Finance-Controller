"""L1 — Deterministic Match. Guide §4.1. The volume layer.

Resolves everything that needs no reasoning, at confidence 1.00, in
milliseconds. The settlement report is the bridge — the only file carrying both
sides — so the join is two hops, not one:

    ledger.order_id ──► settlement.order_id
                        settlement.utr ──► bank.utr

**L1 declares certainty, so L1 must never be wrong.** If a confidence-1.00 match
can be wrong, every confidence below it means nothing and the whole calibration
argument (§2.5) collapses. That is why this layer refuses far more readily than
it matches: anything it cannot fully account for is left for L3, which is
allowed to be uncertain.

Four conditions must all hold before it will claim a credit:

1. The UTR appears exactly once in the bank file. A repeat is the DUPLICATE_UTR
   signal (§4.1 step 5) — matching either copy double-posts revenue.
2. Every order the settlement itemises exists in the ledger.
3. The itemised orders sum exactly to the settlement's stated gross.
4. Nothing was deducted beyond the stated fee and GST.

Condition 4 is not in §4.1, and without it this layer is provably wrong. A
settlement carrying a cross-period refund still passes conditions 1-3 — the
refund is netted out of the payout but never itemised among the order rows, so
`Σ ledger gross == settlement gross` holds — yet the credit is only explained
once the refund is included too. On the seed-42 dataset that is four settlements
claimed with the wrong member set at confidence 1.00. `unitemised_paise` is
exactly that shortfall, and a non-zero value means L3 still has work to do.
"""

from __future__ import annotations

from core.models import MatchProposal, Record
from core.reason_codes import ReasonCode
from matching.protocols import MatchContext

NAME = "L1_exact"


class ExactMatcher:
    """The three-way join. Certain, or silent."""

    name = NAME

    def propose(self, ctx: MatchContext) -> list[MatchProposal]:
        proposals: list[MatchProposal] = []

        for settlement in ctx.settlements:
            detail = settlement.settlement()
            utr = detail.utr

            # -- 1. a repeated UTR is never matched, only flagged ------------
            if ctx.utr_counts.get(utr, 0) > 1:
                ctx.flag(
                    ReasonCode.DUPLICATE_UTR,
                    ref=utr,
                    what=(
                        f"{utr} appears {ctx.utr_counts[utr]} times in the bank "
                        "statement for a single settlement."
                    ),
                    why=(
                        "A banking partner issues one UTR per settlement, so one "
                        "of these credits is not real money. Matching either copy "
                        "would post the revenue twice."
                    ),
                    amount_paise=detail.net.paise,
                    raised_by=self.name,
                )
                continue

            if ctx.is_bank_claimed(utr):
                continue

            # -- the bank leg ------------------------------------------------
            bank_rows = ctx.bank_by_utr.get(utr)
            if not bank_rows:
                ctx.flag(
                    ReasonCode.AWAITING_SETTLEMENT,
                    ref=detail.utr,
                    what=f"Settlement {settlement.external_id} has no bank credit.",
                    why=(
                        "The gateway reported this payout but nothing has landed "
                        "yet. The money is in transit, not missing."
                    ),
                    amount_paise=detail.net.paise,
                    raised_by=self.name,
                )
                continue
            bank = bank_rows[0]

            # -- 2. the ledger leg -------------------------------------------
            ledger_rows = [
                ctx.ledger_by_id[oid]
                for oid in detail.order_ids
                if oid in ctx.ledger_by_id
            ]
            if len(ledger_rows) != len(detail.order_ids):
                missing = [o for o in detail.order_ids if o not in ctx.ledger_by_id]
                ctx.flag(
                    ReasonCode.MISSING_IN_LEDGER,
                    ref=utr,
                    what=(
                        f"{len(missing)} of {len(detail.order_ids)} orders in "
                        f"settlement {settlement.external_id} are absent from the "
                        "ledger."
                    ),
                    why=(
                        "The gateway settled sales the books never recorded. This "
                        "is unrecorded revenue and an audit finding, not a "
                        "matching failure."
                    ),
                    amount_paise=detail.net.paise,
                    raised_by=self.name,
                )
                continue

            # -- 3. the arithmetic must tie exactly --------------------------
            if _gross_of(ledger_rows) != detail.gross.paise:
                continue

            # -- 4. nothing deducted beyond the stated charges ---------------
            if detail.unitemised_paise != 0:
                # Not an error: a real deduction the settlement did not itemise,
                # almost always a prior-period refund (§4.3b). L3 searches a
                # wider refund window for it. L1 simply declines to be certain.
                continue

            # The bank must agree with the settlement about the payout, or the
            # third leg of the join is not actually the same money.
            if bank.amount != detail.net:
                continue

            proposals.append(
                MatchProposal(
                    bank_utr=utr,
                    ledger_ids=frozenset(r.external_id for r in ledger_rows),
                    confidence=1.00,
                    strategy=self.name,
                    settlement_id=settlement.external_id,
                    evidence=("settlement_id", "utr", "order_id", "gross"),
                    reason=(
                        f"Exact three-way join via {settlement.external_id} / "
                        f"{utr}: {len(ledger_rows)} ledger rows sum to "
                        f"{detail.gross}, less {detail.fee} fee and {detail.gst} "
                        f"GST, equals the {detail.net} credit."
                    ),
                )
            )

        return proposals


def _gross_of(rows: list[Record]) -> int:
    """Signed sum, so a refund among the members would subtract, not add."""
    return sum(r.signed_amount for r in rows)
