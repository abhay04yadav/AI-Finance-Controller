"""Confidence routing. Guide §4.5, §2.5.

    >= auto_post_threshold   auto-post      the user sees nothing; it is in the books
    review .. auto           review queue   prepared entry + reason -> a 2-second decision
    <  review_threshold      exception      WHAT / WHY / ACTION card

Both thresholds are read from `Settings` and are DERIVED from the measured
calibration table (§7), never written here by intuition. Hardcoding them in this
file is the gate 8 stop condition — they get tuned from real numbers later, and
a constant buried in a router is a constant nobody re-tunes.
"""

from __future__ import annotations

from enum import StrEnum

from core.config import Settings


class Route(StrEnum):
    AUTO_POST = "auto_post"
    REVIEW = "review"
    EXCEPTION = "exception"


def route_for(confidence: float, settings: Settings) -> Route:
    """Where a match of this confidence belongs."""
    if confidence >= settings.auto_post_threshold:
        return Route.AUTO_POST
    if confidence >= settings.review_threshold:
        return Route.REVIEW
    return Route.EXCEPTION
