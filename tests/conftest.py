"""Shared fixtures. Guide §9.7 (testing pyramid).

    unit         Money, calendar, subset solver (hypothesis property tests), fee model
    integration  full pipeline on a 50-record golden dataset, frozen expected metrics
    contract     LLM response schema against recorded fixtures, runs offline
    regression   make eval in CI; a precision drop fails the build

The BusinessCalendar fixture is defined here and shared by the generator and the
matcher tests. Two different instances make planted HOLIDAY_SHIFT cases
unsolvable by construction (§5.1).
"""


import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _tests_never_reach_the_network() -> "object":
    """No test may call the Anthropic API. Guide §9.7.

    Not a convenience — a correctness rule, and it was added the hard way. The
    moment a real key landed in `.env`, `build_pipeline()` began constructing a
    live `LlmAdjudicator` inside the suite: 698 tests went from 20 seconds to
    128, one asserted `llm_calls == 0` and failed, and roughly thirty verdicts
    were bought and written to the cache by a test run nobody asked to spend
    money.

    A suite whose result depends on whether a developer has a key configured is
    not a suite. Clearing the variable for the session makes `LlmAdjudicator`
    find no credential and decline, which is a supported, tested path — the
    same one a clean clone takes.

    Tests that want to exercise L4 inject a fake client (see
    `tests/unit/test_adjudicator.py`) or read a recorded fixture. Neither needs
    the environment.
    """
    saved = {
        key: os.environ.pop(key, None)
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }
    yield
    for key, value in saved.items():
        if value is not None:
            os.environ[key] = value
