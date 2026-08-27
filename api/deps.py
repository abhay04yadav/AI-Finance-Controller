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

import os
from datetime import date, datetime
from pathlib import Path

from core.dates import IST

#: Where `.env` is looked for: the repo root, beside `.env.example`.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path | None = None) -> bool:
    """Read `.env` into the process environment. Returns whether a file was found.

    Called from entry points only — `api/main.py` and the eval CLI. Never from
    `core/`, `matching/` or anything else the domain touches: a module that
    reads the environment when it is imported cannot be tested, and `core/`
    deliberately has no third-party imports at all.

    **An already-exported variable wins.** `override=False` is the whole point:
    `ANTHROPIC_API_KEY=... make eval` has to beat whatever is in the file, or a
    one-off run silently uses the wrong credential.

    Missing file is not an error. `--no-llm` is a first-class mode (§4.4) and
    gates 0-10 need no key at all, so a clean clone with no `.env` must run.
    """
    target = path or ENV_FILE
    if not target.exists():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Declared in pyproject, so this only happens on a partial install.
        # Reported by the caller rather than swallowed; the run continues
        # without a key, which is a supported state.
        return False
    load_dotenv(target, override=False)
    return True


def has_llm_credential() -> bool:
    """Whether an Anthropic key reached the environment.

    Used only to tell a human what state they are in. The SDK does its own
    resolution — an `ant auth login` profile counts and is invisible here —
    so this answering False does not mean L4 cannot run.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


class SystemClock:
    """The real clock, pinned to Asia/Kolkata.

    Business dates are always IST (§2.7 rule 6). Reading the local date on a
    machine in another timezone would shift settlement windows by a day, and
    reading a UTC date would do the same after 18:30 IST.
    """

    def today(self) -> date:
        return datetime.now(IST).date()

    def now(self) -> datetime:
        """The instant, for stamping an audit event (§9.3).

        Business DATES come from `today()`; this is for "who did what, when",
        which is a different question and needs the time of day to answer.
        """
        return datetime.now(IST)
