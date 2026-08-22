"""Shared fixtures. Guide §9.7 (testing pyramid).

    unit         Money, calendar, subset solver (hypothesis property tests), fee model
    integration  full pipeline on a 50-record golden dataset, frozen expected metrics
    contract     LLM response schema against recorded fixtures, runs offline
    regression   make eval in CI; a precision drop fails the build

The BusinessCalendar fixture is defined here and shared by the generator and the
matcher tests. Two different instances make planted HOLIDAY_SHIFT cases
unsolvable by construction (§5.1).
"""
