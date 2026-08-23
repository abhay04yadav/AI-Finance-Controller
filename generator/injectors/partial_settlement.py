"""AWAITING_SETTLEMENT — partial settlement defers whole transactions.

Models: when the amount due exceeds the gateway's live balance at settlement
time, it settles only the subset of WHOLE transactions that fits and pushes the
rest to the next slot. Razorpay Docs, *About Settlements*; §1.3 phase 5.

It never splits an amount. If ₹1,000 is due across P1 ₹400, P2 ₹400, P3 ₹200 and
only ₹800 is available, it settles P1 + P2 whole and defers P3 whole. A generator
that split amounts instead would make N:1 matching impossible by construction.

This is NOT a failure: the money is genuinely still in transit (Appendix A).
"""

from __future__ import annotations

from random import Random

from core.reason_codes import ReasonCode
from generator.world import TruthException, World


class PartialSettlementInjector:
    reason_code = ReasonCode.AWAITING_SETTLEMENT
    unit = "order"
    ratio = 0.008
    models = (
        "Partial settlement, whole transactions only — "
        "Razorpay *About Settlements* / §1.3 phase 5"
    )

    def inject(self, world: World, rng: Random, count: int) -> list[TruthException]:
        out: list[TruthException] = []
        pool = world.unclaimed_orders(removable=True)
        for batch, deferred in rng.sample(pool, min(count, len(pool))):
            if deferred in world.claimed or len(batch.order_ids) <= 2:
                continue
            world.detach_order(batch, deferred)      # whole order, never a split
            out.append(world.record(deferred, self.reason_code))
        return out
