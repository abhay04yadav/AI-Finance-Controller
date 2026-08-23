"""LATE_AUTHORIZATION — a failed payment resurrects days later.

Models: when the bank returns no response the transaction is marked Failed on
timeout, but the gateway then polls the bank at intervals for 3 days; if a
success arrives the payment moves to Authorized. Razorpay Docs,
*Late Payment Authorisations*; §1.3 phase 3.

A payment the merchant recorded as failed can therefore settle two days later.
The ledger says failed, the bank statement says money arrived, and both are
correct. The order stays in its batch — only the ledger status is wrong.
"""

from __future__ import annotations

from random import Random

from core.reason_codes import ReasonCode
from generator.world import OrderStatus, TruthException, World


class LateAuthorizationInjector:
    reason_code = ReasonCode.LATE_AUTHORIZATION
    unit = "order"
    ratio = 0.006
    models = (
        "Failed-then-authorized via 3-day bank polling — "
        "Razorpay *Late Payment Authorisations* / §1.3 phase 3"
    )

    def inject(self, world: World, rng: Random, count: int) -> list[TruthException]:
        out: list[TruthException] = []
        pool = world.unclaimed_orders()
        for _batch, candidate in rng.sample(pool, min(count, len(pool))):
            if candidate in world.claimed:
                continue
            # Stays in the batch: the money really did arrive. Only the ledger
            # status is stale.
            world.orders[candidate].status = OrderStatus.FAILED
            out.append(world.record(candidate, self.reason_code))
        return out
