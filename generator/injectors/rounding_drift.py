"""ROUNDING_DRIFT — sub-rupee mismatch from fee rounding.

Models: the gateway computes the MDR and then the GST on it with its own
rounding at each step, so inverting a published net will land a few paise away
from the true gross. Razorpay Docs, *Settlements* (settlement reconciliation
report); §4.2 rounding note.

A genuine pair therefore fails an exact-amount match by 1–50 paise. The fix is
NOT to loosen the exact matcher globally — that would hide real mismatches.
It stays a distinct, lower-confidence pass so it remains visible in the metrics.
"""

from __future__ import annotations

from random import Random

from core.reason_codes import ReasonCode
from generator.world import TruthException, World


class RoundingDriftInjector:
    reason_code = ReasonCode.ROUNDING_DRIFT
    unit = "batch"
    ratio = 0.08
    models = (
        "Per-step fee and GST rounding in the settlement report — "
        "Razorpay *Settlements* / §4.2"
    )

    def inject(self, world: World, rng: Random, count: int) -> list[TruthException]:
        out: list[TruthException] = []
        pool = world.unclaimed_batches(min_orders=1)
        for batch in rng.sample(pool, min(count, len(pool))):
            drift = rng.randint(1, 50) * rng.choice((-1, 1))
            batch.fee_adjustment_paise = drift
            world.claim(batch.settlement_id)
            out.append(world.record(batch.utr, self.reason_code))
        return out
