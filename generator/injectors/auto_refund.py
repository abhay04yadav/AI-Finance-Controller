"""AUTO_REFUNDED — authorized but never captured, so auto-refunded.

Models: payments not captured within 3 days of creation are automatically
refunded to the customer. Razorpay Docs, *Payment Capture Settings*;
§1.3 phase 3.

The ledger shows a sale that will never settle. A naive matcher reports it as
missing money; it is money that was returned to the customer.
"""

from __future__ import annotations

from random import Random

from core.reason_codes import ReasonCode
from generator.world import OrderStatus, TruthException, World


class AutoRefundedInjector:
    reason_code = ReasonCode.AUTO_REFUNDED
    unit = "order"
    ratio = 0.004
    models = "3-day capture-or-auto-refund — Razorpay *Payment Capture Settings* / §1.3 phase 3"

    def inject(self, world: World, rng: Random, count: int) -> list[TruthException]:
        out: list[TruthException] = []
        pool = world.unclaimed_orders(removable=True)
        for batch, candidate in rng.sample(pool, min(count, len(pool))):
            if candidate in world.claimed or len(batch.order_ids) <= 2:
                continue
            world.detach_order(batch, candidate)
            world.orders[candidate].status = OrderStatus.AUTHORIZED   # never captured
            out.append(world.record(candidate, self.reason_code))
        return out
