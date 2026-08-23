"""DUPLICATE_UTR — the same credit line appears twice in the bank file.

Models: a statement export run twice over an overlapping date range, or a bank
feed replaying a row. The UTR is the unique reference the banking partner issues
per settlement (§1.3 phase 5), so the same UTR appearing on two credit lines
means one of them is not real money.

The danger is specific: matching both double-posts revenue. L1 must refuse to
match a duplicated UTR at all and flag it immediately (§4.1 step 5), rather than
picking one and moving on.
"""

from __future__ import annotations

from random import Random

from core.reason_codes import ReasonCode
from generator.world import TruthException, World


class DuplicateUtrInjector:
    reason_code = ReasonCode.DUPLICATE_UTR
    unit = "batch"
    ratio = 0.04
    models = (
        "One UTR per settlement from the banking partner — "
        "Razorpay *Settlements* / §1.3 phase 5"
    )

    def inject(self, world: World, rng: Random, count: int) -> list[TruthException]:
        out: list[TruthException] = []
        pool = [b for b in world.unclaimed_batches(min_orders=1) if b.order_ids]
        for batch in rng.sample(pool, min(count, len(pool))):
            world.duplicated_utrs.add(batch.utr)   # emitted twice at write time
            world.claim(batch.settlement_id)
            out.append(world.record(batch.utr, self.reason_code))
        return out
