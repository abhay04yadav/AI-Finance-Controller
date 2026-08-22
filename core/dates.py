"""Business-date calendar and DateWindow. Guide §5.1, §9.2.

Two rules this module exists to enforce:
  - All dates are business dates in Asia/Kolkata, stored as `date`.
  - The calendar is INJECTED, never global. The generator and the matcher must
    share one instance or planted HOLIDAY_SHIFT cases become unsolvable by
    construction (§5.1, Review Guide gate 1).
  - Never call date.today() in business logic — inject a Clock (§9.2).
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class DateWindow:
    """Inclusive [start, end] range of business dates."""

    start: date
    end: date

    def contains(self, d: date) -> bool:
        raise NotImplementedError("Gate 1 — §5.1")


class BusinessCalendar:
    """Sundays, 2nd/4th Saturdays, and a configurable holiday set. Injected, not global."""

    def add_business_days(self, d: date, n: int) -> date:
        raise NotImplementedError("Gate 1 — §5.1")

    def window_back(self, d: date, n: int) -> DateWindow:
        raise NotImplementedError("Gate 1 — §5.1")


class Clock:
    """Injected source of 'now'. Exists so business logic never reads the wall clock (§9.2)."""

    def today(self) -> date:
        raise NotImplementedError("Gate 1 — §9.2")
