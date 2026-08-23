"""DI container — the only place concretes are named. Guide §5.4 (DIP), §5.9.

Swapping the LLM provider is one line here; the orchestrator, matchers, and
tests stay untouched.

This is also where the SINGLE BusinessCalendar instance is constructed and handed
to both the generator and the matcher (§5.1). Two different calendar instances
make planted HOLIDAY_SHIFT cases unsolvable by construction, and that bug looks
exactly like a matcher failure.

The wiring itself arrives with the pipeline. What lives here at gate 1 is
`SystemClock`: the one place in the codebase permitted to read the wall clock.
It sits here rather than in `core/` deliberately — `core/` must contain no
wall-clock read at all, which the standing drift check enforces, and the
composition root is where impure edges belong.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.dates import IST_TZ_NAME

IST = ZoneInfo(IST_TZ_NAME)


class SystemClock:
    """The real clock, pinned to Asia/Kolkata.

    Business dates are always IST (§2.7 rule 6). Reading the local date on a
    machine in another timezone would shift settlement windows by a day, and
    reading a UTC date would do the same after 18:30 IST.
    """

    def today(self) -> date:
        return datetime.now(IST).date()
