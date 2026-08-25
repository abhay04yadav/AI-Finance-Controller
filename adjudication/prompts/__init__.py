"""Versioned prompt files. Guide §4.4: "version the prompt file and log the
version with every verdict".

PROMPT_VERSION is a content hash, not a hand-maintained number. A number gets
forgotten the one time it matters — someone edits a sentence in the prompt,
leaves the version alone, and every stale cache entry silently answers the new
question. The hash cannot be forgotten: change a character and every cache key
changes with it.

The version is stamped on every Verdict and Hypothesis, so an audit trail can
answer "which prompt produced this decision?" months later.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent

JOB_A = "job_a_select.md"
JOB_B = "job_b_classify.md"


@lru_cache(maxsize=8)
def load(name: str) -> str:
    """Read one prompt file. Newlines normalised so a checkout with CRLF
    endings produces the same version hash as one with LF — otherwise the cache
    misses on Windows and hits on CI, and the two disagree about the numbers."""
    return (_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n")


@lru_cache(maxsize=8)
def version(name: str) -> str:
    """A short content hash of one prompt file."""
    digest = hashlib.sha256(load(name).encode("utf-8")).hexdigest()
    return f"{name.removesuffix('.md')}@{digest[:12]}"
