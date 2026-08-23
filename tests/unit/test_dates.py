"""Business calendar, DateWindow and the injected clock. Guide §5.1, §9.2.

The calendar decides where the T+2 settlement window falls, so an error here
does not look like a date bug — it looks like a matcher failure.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

from core.dates import BusinessCalendar, Clock, DateWindow, FixedClock

# August 2026 reference points, verified against the calendar:
#   Sat 01 (1st Sat, working)    Sun 02 (closed)
#   Sat 08 (2nd Sat, closed)     Sat 15 (3rd Sat, working)
#   Sat 22 (4th Sat, closed)     Sat 29 (5th Sat, working)
MON_3 = date(2026, 8, 3)
SAT_1ST = date(2026, 8, 1)
SAT_2ND = date(2026, 8, 8)
SAT_3RD = date(2026, 8, 15)
SAT_4TH = date(2026, 8, 22)
SAT_5TH = date(2026, 8, 29)
SUN = date(2026, 8, 2)


@pytest.fixture
def cal() -> BusinessCalendar:
    return BusinessCalendar()


# --------------------------------------------------------------------------
# Which days the banks are open
# --------------------------------------------------------------------------


def test_sunday_is_closed(cal: BusinessCalendar) -> None:
    assert not cal.is_business_day(SUN)


def test_second_and_fourth_saturdays_are_closed(cal: BusinessCalendar) -> None:
    assert not cal.is_business_day(SAT_2ND)
    assert not cal.is_business_day(SAT_4TH)


def test_first_third_and_fifth_saturdays_are_working_days(cal: BusinessCalendar) -> None:
    """The rule is 2nd and 4th only. Treating every Saturday as closed shifts
    T+2 by a day on those weeks and silently breaks matching."""
    assert cal.is_business_day(SAT_1ST)
    assert cal.is_business_day(SAT_3RD)
    assert cal.is_business_day(SAT_5TH)


def test_weekdays_are_working_days(cal: BusinessCalendar) -> None:
    assert all(
        cal.is_business_day(date(2026, 8, d)) for d in (3, 4, 5, 6, 7)
    )  # Mon-Fri


def test_holidays_are_closed() -> None:
    independence_day = date(2026, 8, 15)  # also a 3rd Saturday, normally working
    cal = BusinessCalendar(holidays=[independence_day])
    assert not cal.is_business_day(independence_day)
    assert BusinessCalendar().is_business_day(independence_day)


def test_saturday_ordinal_boundaries(cal: BusinessCalendar) -> None:
    """Day 8 is the 2nd of its weekday, day 14 the 2nd only if it is a Saturday."""
    assert cal._saturday_ordinal(date(2026, 8, 1)) == 1
    assert cal._saturday_ordinal(date(2026, 8, 7)) == 1
    assert cal._saturday_ordinal(date(2026, 8, 8)) == 2
    assert cal._saturday_ordinal(date(2026, 8, 22)) == 4
    assert cal._saturday_ordinal(date(2026, 8, 29)) == 5


# --------------------------------------------------------------------------
# Walking the calendar — this is what T+2 means
# --------------------------------------------------------------------------


def test_add_zero_business_days_is_identity(cal: BusinessCalendar) -> None:
    assert cal.add_business_days(MON_3, 0) == MON_3
    assert cal.add_business_days(SUN, 0) == SUN


def test_t_plus_2_over_a_plain_week(cal: BusinessCalendar) -> None:
    assert cal.add_business_days(MON_3, 2) == date(2026, 8, 5)  # Mon -> Wed


def test_t_plus_2_skips_the_weekend(cal: BusinessCalendar) -> None:
    thu = date(2026, 8, 6)
    assert cal.add_business_days(thu, 2) == date(2026, 8, 10)  # Thu -> Mon


def test_t_plus_2_skips_a_closed_saturday_and_sunday(cal: BusinessCalendar) -> None:
    """Fri 07 + 2: Sat 08 is a 2nd Saturday and Sun 09 is a Sunday, so both are
    skipped and settlement lands on Tue 11."""
    fri = date(2026, 8, 7)
    assert cal.add_business_days(fri, 2) == date(2026, 8, 11)


def test_t_plus_2_counts_a_working_saturday(cal: BusinessCalendar) -> None:
    """Fri 14 + 2: Sat 15 is a 3rd Saturday and counts; Sun 16 does not."""
    fri = date(2026, 8, 14)
    assert cal.add_business_days(fri, 2) == date(2026, 8, 17)


def test_holiday_extends_the_settlement_window() -> None:
    cal = BusinessCalendar(holidays=[date(2026, 8, 5)])
    assert cal.add_business_days(MON_3, 2) == date(2026, 8, 6)


def test_add_always_lands_on_a_business_day(cal: BusinessCalendar) -> None:
    for offset in range(60):
        start = date(2026, 8, 1) + timedelta(days=offset)
        assert cal.is_business_day(cal.add_business_days(start, 2))


def test_subtract_is_the_inverse_of_add(cal: BusinessCalendar) -> None:
    """Back-solving the capture window from a credit date depends on this."""
    for day in range(1, 29):
        d = date(2026, 8, day)
        if not cal.is_business_day(d):
            continue
        assert cal.subtract_business_days(cal.add_business_days(d, 2), 2) == d


def test_negative_n_is_refused(cal: BusinessCalendar) -> None:
    with pytest.raises(ValueError):
        cal.add_business_days(MON_3, -1)


def test_next_business_day_shifts_a_closed_day(cal: BusinessCalendar) -> None:
    """The HOLIDAY_SHIFT mechanic: a settlement computing to Sunday lands Monday."""
    assert cal.next_business_day(SUN) == MON_3
    assert cal.next_business_day(SAT_2ND) == date(2026, 8, 10)
    assert cal.next_business_day(MON_3) == MON_3  # already open


def test_business_days_between_matches_add(cal: BusinessCalendar) -> None:
    assert cal.business_days_between(MON_3, cal.add_business_days(MON_3, 5)) == 5
    assert cal.business_days_between(MON_3, MON_3) == 0


def test_a_pathological_holiday_set_raises_rather_than_hanging() -> None:
    every_day = [date(2026, 8, 1) + timedelta(days=i) for i in range(4000)]
    cal = BusinessCalendar(holidays=every_day)
    with pytest.raises(ValueError, match="10 years"):
        cal.add_business_days(date(2026, 8, 1), 1)


# --------------------------------------------------------------------------
# The calendar is injected, never global
# --------------------------------------------------------------------------


def test_calendar_requires_construction() -> None:
    """No module-level singleton to reach for. §5.1: the generator and the
    matcher must share one explicitly-passed instance."""
    import core.dates as dates_module

    exported = {
        name
        for name, obj in vars(dates_module).items()
        if isinstance(obj, BusinessCalendar)
    }
    assert not exported, f"module-level calendar instance found: {exported}"


def test_holidays_are_per_instance() -> None:
    a = BusinessCalendar(holidays=[date(2026, 8, 5)])
    b = BusinessCalendar()
    assert not a.is_business_day(date(2026, 8, 5))
    assert b.is_business_day(date(2026, 8, 5))


def test_holiday_set_is_not_mutable_from_outside() -> None:
    holidays = [date(2026, 8, 5)]
    cal = BusinessCalendar(holidays=holidays)
    holidays.append(date(2026, 8, 6))
    assert cal.is_business_day(date(2026, 8, 6))


# --------------------------------------------------------------------------
# DateWindow
# --------------------------------------------------------------------------


def test_window_contains_is_inclusive() -> None:
    w = DateWindow(date(2026, 8, 3), date(2026, 8, 7))
    assert w.contains(date(2026, 8, 3))
    assert w.contains(date(2026, 8, 7))
    assert w.contains(date(2026, 8, 5))
    assert not w.contains(date(2026, 8, 2))
    assert not w.contains(date(2026, 8, 8))


def test_window_of_a_single_day() -> None:
    w = DateWindow(MON_3, MON_3)
    assert w.contains(MON_3)
    assert w.days == 1


def test_inverted_window_is_refused() -> None:
    with pytest.raises(ValueError):
        DateWindow(date(2026, 8, 7), date(2026, 8, 3))


def test_window_is_immutable() -> None:
    w = DateWindow(MON_3, date(2026, 8, 7))
    with pytest.raises(FrozenInstanceError):
        w.start = date(2026, 1, 1)  # type: ignore[misc]


def test_window_back_spans_the_capture_period(cal: BusinessCalendar) -> None:
    """Given a credit date, the orders that could have produced it."""
    w = cal.window_back(date(2026, 8, 5), 2)
    assert w.end == date(2026, 8, 5)
    assert w.start == MON_3
    assert w.contains(date(2026, 8, 4))


# --------------------------------------------------------------------------
# The clock is injected; core reads no wall clock
# --------------------------------------------------------------------------


def test_fixed_clock_returns_its_date() -> None:
    assert FixedClock(MON_3).today() == MON_3


def test_fixed_clock_satisfies_the_protocol() -> None:
    clock: Clock = FixedClock(MON_3)
    assert clock.today() == MON_3


def test_core_dates_never_reads_the_wall_clock() -> None:
    """§9.2. The system clock lives at the composition root, not in core/.

    Uses the same tokenizer as the standing drift check, which reads CODE only —
    a naive text scan matches the rule as written in this module's own docstring
    just as readily as it matches a real violation.
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "drift_check", root / "scripts" / "drift_check.py"
    )
    assert spec and spec.loader
    drift = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drift)

    hits = drift.scan(
        [root / "core" / "dates.py"],
        r"(date\s*\.\s*today|datetime\s*\.\s*(now|today)|time\s*\.\s*time)\s*\(",
        exclude=r"def\s+today",
    )
    assert not hits, "wall-clock read in core/dates.py: " + "; ".join(hits)


def test_system_clock_lives_at_the_composition_root() -> None:
    """The one permitted wall-clock read, pinned to IST (§2.7 rule 6)."""
    from api.deps import IST, SystemClock

    assert str(IST) == "Asia/Kolkata"
    today = SystemClock().today()
    assert isinstance(today, date)
