"""HOLIDAY_SHIFT — T+2 lands on a non-working day and settlement slips.

Models: the settlement cycle is T+2 *working* days, where working days exclude
Sundays, the 2nd and 4th Saturday of each month, and bank holidays. Razorpay
Docs, *Settlements* / *Settlements FAQs*; §1.3 phase 5.

Nothing is corrupted here — the credit is simply later than naive arithmetic
predicts, so a matcher using `capture_date + 2 calendar days` finds nothing
(§1.5). This injector plants no new data: it labels the batches the calendar has
already pushed, which is why it must run against a real business calendar rather
than inventing a shift.

Because both the generator and the matcher share one injected BusinessCalendar
(§5.1), these cases are solvable — by a calendar-aware matcher, and only by one.
"""

from __future__ import annotations

from datetime import timedelta
from random import Random

from core.reason_codes import ReasonCode
from generator.world import TruthException, World


class HolidayShiftInjector:
    reason_code = ReasonCode.HOLIDAY_SHIFT
    unit = "batch"
    ratio = 0.08
    models = (
        "T+2 working-day cycle, weekend and holiday exclusions — "
        "Razorpay *Settlements FAQs* / §1.3 phase 5"
    )

    def inject(self, world: World, rng: Random, count: int) -> list[TruthException]:
        out: list[TruthException] = []
        # A batch is shifted when its settle date is later than a naive
        # capture_date + 2 calendar days would put it.
        shifted = [
            b
            for b in world.unclaimed_batches()
            if b.order_ids
            and b.settle_date
            > min(world.orders[o].capture_date for o in b.order_ids) + timedelta(days=2)
        ]
        for batch in rng.sample(shifted, min(count, len(shifted))):
            batch.holiday_shifted = True
            world.claim(batch.settlement_id)
            out.append(world.record(batch.utr, self.reason_code))
        return out
