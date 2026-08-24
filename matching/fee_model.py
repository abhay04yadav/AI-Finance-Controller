"""L2 — Fee Model Inference. Guide §4.2, §2.3.

The MDR is merchant-specific — 1.75%, 2.0%, 2.35%, negotiated — and most
merchants cannot state it. Asking makes the tool useless until configured;
hardcoding 2% fails on every other merchant's data. So it is **derived**:

    net   = gross − gross·r − 0.18·gross·r
          = gross · (1 − 1.18r)

    r     = (1 − net/gross) / 1.18      infer, from pairs we are certain about
    gross = net / (1 − 1.18r)           invert, for credits we are not

This is what lets the system run on any merchant's export with zero setup, and
it is what gives L3 a target it can actually search for: without it, a bank
credit of ₹7,811.20 has no relationship to a ledger row of ₹8,000.

**Median, never mean.** One international-card settlement at 3.5% drags a mean
and then mis-prices every credit downstream. §4.2 step 2.

**No configured rate exists in the normal path.** `Settings` deliberately holds
no MDR — only the statutory GST rate, which is law rather than negotiation. The
`0.02` below is reachable only when there are too few samples to infer anything,
and a model in that state is marked LOW_CONFIDENCE so L3 widens its tolerance
instead of trusting a guess.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

#: §4.2 step 5. Below this many clean pairs, inference is not credible.
MIN_SAMPLES: Final = 5

#: Statutory GST on the gateway fee. Law, not a negotiated rate, so it is known.
DEFAULT_GST_RATE: Final = 0.18

#: Used ONLY when there are too few samples to infer. Always accompanied by
#: LOW_CONFIDENCE — a model carrying this has not learned anything.
FALLBACK_RATE: Final = 0.02

#: Two settlements whose implied rates differ by more than this are on different
#: MDR slabs, not the same slab with rounding noise. Rounding drift moves an
#: implied rate by ~1e-6; a slab difference is ~1e-2, so the threshold sits far
#: from both.
SLAB_GAP: Final = 0.003

#: Rates a merchant plausibly negotiated, for the §4.2 step 4 snap.
PLAUSIBLE_SLABS: Final = (
    0.0175, 0.018, 0.019, 0.02, 0.0212, 0.022, 0.0236, 0.025, 0.03, 0.035,
)

#: How close the raw rate must be to a plausible slab before it is reported as
#: one. Deliberately far tighter than §4.2's "within tolerance": inference noise
#: is around 4e-7, so anything looser starts renaming merchants. At 5e-4 a
#: genuine 2.35% merchant was reported as "a standard 2.36% slab" — harmless to
#: the arithmetic, but a false sentence to say in front of a judge.
SNAP_TOLERANCE: Final = 1e-5


class FeeConfidence(StrEnum):
    INFERRED = "inferred"
    LOW_CONFIDENCE = "low_confidence"


@dataclass(frozen=True, slots=True)
class FeeModel:
    """The merchant's fee structure, learned from settlements we are sure about."""

    rate: float
    gst_rate: float = DEFAULT_GST_RATE
    sample_size: int = 0
    dispersion: float = 0.0
    slabs: tuple[float, ...] = ()
    confidence: FeeConfidence = FeeConfidence.INFERRED
    outliers: tuple[float, ...] = field(default=(), compare=False)

    # ------------------------------------------------------------- inference

    @classmethod
    def infer(cls, pairs: list[tuple[int, int]], *, gst_rate: float = DEFAULT_GST_RATE) -> FeeModel:
        """Learn the rate from confirmed (gross, net) pairs.

        The pairs must come from matches we are certain about — L1's, where the
        stated charges fully account for the payout. A settlement carrying an
        unitemised cross-period refund would read as a much higher fee and
        poison the sample; L1 already refuses those, so its output is clean by
        construction.
        """
        rates = [
            (1 - net / gross) / (1 + gst_rate)
            for gross, net in pairs
            if gross > 0
        ]

        if len(rates) < MIN_SAMPLES:
            return cls(
                rate=FALLBACK_RATE,
                gst_rate=gst_rate,
                sample_size=len(rates),
                dispersion=math.inf,
                confidence=FeeConfidence.LOW_CONFIDENCE,
            )

        clusters = _cluster(rates)
        # The merchant's own rate is the one most of their money moves at. Taking
        # the median of the largest cluster rather than of everything means a
        # block of international settlements cannot shift it even if it is large
        # enough to reach past the midpoint.
        dominant = max(clusters, key=len)
        outliers = tuple(
            sorted(r for c in clusters if c is not dominant for r in c)
        )

        return cls(
            rate=statistics.median(dominant),
            gst_rate=gst_rate,
            sample_size=len(rates),
            dispersion=_iqr(rates),
            slabs=tuple(round(statistics.median(c), 6) for c in clusters),
            confidence=FeeConfidence.INFERRED,
            outliers=outliers,
        )

    @classmethod
    def disabled(cls) -> FeeModel:
        """The --no-fee-model ablation (§7.5): no rate was learned at all."""
        return cls(
            rate=FALLBACK_RATE,
            sample_size=0,
            dispersion=math.inf,
            confidence=FeeConfidence.LOW_CONFIDENCE,
        )

    # -------------------------------------------------------------- reporting

    @property
    def snapped_rate(self) -> float | None:
        """The nearest plausible negotiated slab, if the raw rate is close.

        §4.2 step 4 asks for this and says to record both the raw and snapped
        value. It is recorded and reported — but deliberately NOT used for
        arithmetic, because snapping can only ever move the rate away from what
        the data says.

        Measured on this generator: a raw median recovers the planted rate to
        about 4e-7. Snapping a genuine 2.35% merchant to the plausible 2.36%
        introduces 1e-4 — roughly 250 times worse — which is 236 paise of net
        error on a ₹20,000 settlement, past the 50-paise round-trip tolerance
        §4.2 itself sets. So the raw value drives the matching and this one
        drives the sentence in the demo.
        """
        if self.confidence is FeeConfidence.LOW_CONFIDENCE:
            return None
        nearest = min(PLAUSIBLE_SLABS, key=lambda s: abs(s - self.rate))
        return nearest if abs(nearest - self.rate) <= SNAP_TOLERANCE else None

    @property
    def is_multi_slab(self) -> bool:
        return len(self.slabs) > 1

    @property
    def is_usable(self) -> bool:
        return self.confidence is FeeConfidence.INFERRED

    def describe(self) -> str:
        """The §4.2 demo line: we were never told the MDR."""
        if not self.is_usable:
            return (
                f"MDR not inferred — only {self.sample_size} confident "
                f"settlement(s), fewer than the {MIN_SAMPLES} required"
            )
        snapped = self.snapped_rate
        line = (
            f"Inferred MDR: {self.rate:.3%} from {self.sample_size} confident "
            "settlements"
        )
        if snapped is not None:
            line += f" (a standard {snapped:.2%} slab)"
        if self.is_multi_slab:
            # Compared with a tolerance, not equality: `slabs` holds rounded
            # cluster medians while `rate` is the raw one, so `!=` would list
            # the merchant's own rate as one of the "other" slabs.
            others = ", ".join(
                f"{s:.2%}" for s in self.slabs if abs(s - self.rate) > SLAB_GAP
            )
            if others:
                line += (
                    f"; {len(self.outliers)} settlement(s) on other slabs: {others}"
                )
        return line

    # ------------------------------------------------------------- arithmetic

    def expected_gross(self, net_paise: int) -> int:
        """Invert a bank credit back to what was charged. L3's search target."""
        divisor = 1 - (1 + self.gst_rate) * self.rate
        if divisor <= 0:
            raise ValueError(f"implausible fee rate {self.rate}")
        return round(net_paise / divisor)

    def expected_net(self, gross_paise: int) -> int:
        """What should land in the bank for this gross.

        Truncates at each step, the way a gateway does: the fee is computed and
        rounded, then GST is computed on the rounded fee. Doing it in one
        multiplication would drift by a paisa or two and turn genuine pairs into
        near-misses.
        """
        fee = int(gross_paise * self.rate)
        return gross_paise - fee - int(fee * self.gst_rate)

    def tolerance_paise(self, base: int) -> int:
        """How far a candidate may miss before it stops being a candidate.

        A LOW_CONFIDENCE model has not learned the rate, so L3 must search a
        wider band rather than pretend the fallback is right (§4.2 step 5).
        """
        return base if self.is_usable else base * 20


def _cluster(rates: list[float], gap: float = SLAB_GAP) -> list[list[float]]:
    """Split sorted rates wherever the step between them exceeds `gap`."""
    ordered = sorted(rates)
    clusters: list[list[float]] = [[ordered[0]]]
    for rate in ordered[1:]:
        if rate - clusters[-1][-1] > gap:
            clusters.append([rate])
        else:
            clusters[-1].append(rate)
    return clusters


def _iqr(values: list[float]) -> float:
    """Interquartile range — the dispersion §4.2 step 3 asks for."""
    if len(values) < 4:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    lower = ordered[:mid]
    upper = ordered[mid + 1 :] if len(ordered) % 2 else ordered[mid:]
    return statistics.median(upper) - statistics.median(lower)
