"""MISSING_IN_LEDGER — money in the bank with no ledger entry.

Models: a sale that reached the gateway but was never recorded in the merchant's
books — a webhook the merchant's server dropped, or an order created outside the
normal flow. The gateway's settlement report knows about it; the ledger does not.

This is unrecorded revenue and a genuine audit finding (§1.5), not a matching
failure. The settlement rows are emitted so the agent can diagnose *what* the
money was; the ledger rows are withheld so it can see that the books are short.
"""

from __future__ import annotations

from random import Random

from core.reason_codes import ReasonCode
from generator.world import TruthException, World


class MissingInLedgerInjector:
    reason_code = ReasonCode.MISSING_IN_LEDGER
    unit = "batch"
    ratio = 0.04
    models = "Unrecorded revenue — gateway settled a sale the ledger never captured / §1.5"

    def inject(self, world: World, rng: Random, count: int) -> list[TruthException]:
        out: list[TruthException] = []
        # Withholding ledger rows would orphan any anomaly already planted on a
        # member of this batch, so only untouched settlements are eligible.
        pool = [
            b
            for b in world.unclaimed_batches(min_orders=1)
            if not (set(b.order_ids) | set(b.refund_ids)) & world.claimed
        ]
        for batch in rng.sample(pool, min(count, len(pool))):
            # Settlement and bank rows stay; the ledger rows are withheld.
            batch.orders_hidden_from_ledger = True
            world.claim(batch.settlement_id)
            out.append(world.record(batch.utr, self.reason_code))
        return out
