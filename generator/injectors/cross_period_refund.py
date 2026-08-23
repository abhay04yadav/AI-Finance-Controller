"""CROSS_PERIOD_REFUND — a prior-period refund netted out of this batch.

Models: refunds are deducted from the settlement in which they clear, not the
one containing the original sale, so a refund weeks old reduces today's payout.
Razorpay Docs, *Settlements*; §1.3 phase 5, §1.4 reason 4.

The refund appears in the ledger dated in a prior period and is deducted from
the batch net, but is deliberately NOT itemised among that settlement's order
rows. That is what makes the batch total "unexplainably short" (§1.5) and what
forces L3 to search a wider refund window than the T+2 order window (§4.3b).
"""

from __future__ import annotations

from datetime import timedelta
from random import Random

from core.reason_codes import ReasonCode
from generator.world import Refund, TruthException, World


class CrossPeriodRefundInjector:
    reason_code = ReasonCode.CROSS_PERIOD_REFUND
    unit = "order"
    ratio = 0.008
    models = "Refunds netted from the clearing settlement — Razorpay *Settlements* / §1.4 reason 4"

    def inject(self, world: World, rng: Random, count: int) -> list[TruthException]:
        out: list[TruthException] = []
        pool = world.unclaimed_orders()
        for batch, original in rng.sample(pool, min(count, len(pool))):
            if original in world.claimed:
                continue
            # Dated well before this batch — the whole point is that it predates
            # the T+2 window and is only findable in a wider lookback.
            age = rng.randint(20, 40)
            refund_date = batch.settle_date - timedelta(days=age)
            amount = min(
                world.orders[original].amount_paise,
                rng.randrange(20_000, 120_000, 100),
            )
            refund = Refund(
                refund_id=world.next_refund_id(),
                amount_paise=amount,
                refund_date=refund_date,
                original_order_id=original,
            )
            world.refunds[refund.refund_id] = refund
            batch.refund_ids.append(refund.refund_id)
            world.claim(original)   # one cross-period refund per order
            out.append(world.record(refund.refund_id, self.reason_code))
        return out
