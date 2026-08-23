"""Business dates, the settlement calendar, and the injected clock.
Guide §5.1, §2.7 rule 6, §9.2.

Three rules this module exists to enforce:

1. All dates are business dates in Asia/Kolkata, stored as `date`. No naive
   datetimes, no UTC drift.
2. The calendar is **injected, never global**. The generator and the matcher
   must share one instance, or planted HOLIDAY_SHIFT cases become unsolvable by
   construction — a bug that looks exactly like a matcher failure.
3. Business logic never reads the wall clock. `Clock` is a protocol; the only
   implementation here is `FixedClock`, which is pure. The system clock lives at
   the composition root (`api/deps.py`), so a settlement window can never depend
   on what time it happens to be, or on the timezone of whoever runs the demo.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Protocol
from zoneinfo import ZoneInfo

SATURDAY: Final = 5
SUNDAY: Final = 6

#: Settlement windows are small; this only guards against a pathological
#: holiday set making the day-stepping loops run away.
_MAX_STEPS: Final = 3650

IST_TZ_NAME: Final = "Asia/Kolkata"

#: Business dates are always Asia/Kolkata (§2.7 rule 6). Defined once, here,
#: so ingest and the composition root cannot disagree about what "today" means.
IST: Final = ZoneInfo(IST_TZ_NAME)


@dataclass(frozen=True, slots=True)
class DateWindow:
    """An inclusive [start, end] range of dates."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"window start {self.start} is after end {self.end}")

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    @property
    def days(self) -> int:
        """Calendar days spanned, inclusive of both ends."""
        return (self.end - self.start).days + 1

    def __str__(self) -> str:
        return f"[{self.start.isoformat()} : {self.end.isoformat()}]"


class BusinessCalendar:
    """Indian banking calendar: Sundays, 2nd/4th Saturdays, and holidays.

    Constructed with an explicit holiday set and passed down — never reached for
    as a module-level singleton. See the module docstring for why that matters.
    """

    __slots__ = ("_holidays",)

    def __init__(self, holidays: Iterable[date] = ()) -> None:
        self._holidays = frozenset(holidays)

    @property
    def holidays(self) -> frozenset[date]:
        return self._holidays

    # ------------------------------------------------------------- predicates

    @staticmethod
    def _saturday_ordinal(d: date) -> int:
        """Which Saturday of the month this is — 1st, 2nd, 3rd, 4th or 5th."""
        return (d.day - 1) // 7 + 1

    def is_business_day(self, d: date) -> bool:
        """A working day for settlement purposes.

        Banks are closed on Sundays and on the 2nd and 4th Saturday of each
        month. The 1st, 3rd and 5th Saturdays are working days — getting this
        wrong shifts T+2 by a day and breaks matching on those weeks.
        """
        weekday = d.weekday()
        if weekday == SUNDAY:
            return False
        if weekday == SATURDAY and self._saturday_ordinal(d) in (2, 4):
            return False
        return d not in self._holidays

    # ---------------------------------------------------------------- walking

    def _step(self, d: date, n: int, direction: int) -> date:
        if n < 0:
            raise ValueError("n must be non-negative; use the opposite method")
        current, remaining, steps = d, n, 0
        while remaining > 0:
            current += timedelta(days=direction)
            steps += 1
            if steps > _MAX_STEPS:
                raise ValueError(
                    "no business day found within 10 years — check the holiday set"
                )
            if self.is_business_day(current):
                remaining -= 1
        return current

    def add_business_days(self, d: date, n: int) -> date:
        """The date `n` business days after `d`.

        `n=0` returns `d` unchanged, even if `d` is itself a holiday — the
        caller decides whether the starting point is valid.
        """
        return self._step(d, n, +1)

    def subtract_business_days(self, d: date, n: int) -> date:
        """The date `n` business days before `d`."""
        return self._step(d, n, -1)

    def next_business_day(self, d: date) -> date:
        """`d` if it is a working day, otherwise the next one.

        This is the HOLIDAY_SHIFT mechanic: a settlement that computes to a
        Sunday actually lands on the following Monday.
        """
        return d if self.is_business_day(d) else self.add_business_days(d, 1)

    def window_back(self, d: date, n: int) -> DateWindow:
        """The window covering `n` business days back from `d`, inclusive."""
        return DateWindow(self.subtract_business_days(d, n), d)

    def business_days_between(self, start: date, end: date) -> int:
        """Count business days in (start, end] — the inverse of add_business_days."""
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        count, current = 0, start
        while current < end:
            current += timedelta(days=1)
            if self.is_business_day(current):
                count += 1
        return count


class Clock(Protocol):
    """Injected source of the current business date.

    Exists so no layer ever calls `date.today()`. Settlement windows computed
    against the wall clock break when the demo runs at 11pm, or on a machine in
    another timezone (§9.2).
    """

    def today(self) -> date: ...


@dataclass(frozen=True, slots=True)
class FixedClock:
    """A clock pinned to one date. The default everywhere except production.

    Keeping the only in-`core` implementation pure means `core/` contains no
    wall-clock read at all, which the standing drift check enforces.
    """

    _today: date

    def today(self) -> date:
        return self._today
