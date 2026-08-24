"""Metrics and the marksheet. Guide §7.2, §7.4.

The numbers this prints are the submission. Everything else is supporting
evidence.

Two of these matter more than the rest, and they are deliberately reported
together because reporting only the first is how a broken system looks good:

    match rate      = attempted / total       how much it answered
    match precision = correct   / attempted   how much of that was right

    Team A answers 95 of 100, gets 94 right  -> rate  95.0%, precision 98.9%
    Team B answers all 100,   gets 82 right  -> rate 100.0%, precision 82.0%

Team B's dashboard looks better and their books are wrecked: 18 wrong pairs
posted as journal entries. In finance a wrong answer is worse than "I don't
know", so precision is the headline and rate is the context.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from core.console import glyph
from core.console import money as money_str
from core.console import rule as console_rule

#: Confidence bands from §2.5 / §7.4. The top band is the auto-post band, and
#: its precision is the number a controller actually cares about: "everything
#: you posted without asking me — that's all correct, right?"
BUCKET_EDGES: tuple[tuple[str, float, float], ...] = (
    ("0.95 - 1.00", 0.95, 1.01),
    ("0.85 - 0.95", 0.85, 0.95),
    ("0.70 - 0.85", 0.70, 0.85),
    ("below 0.70", -0.01, 0.70),
)


def bucket_of(confidence: float) -> str:
    for label, lo, hi in BUCKET_EDGES:
        if lo <= confidence < hi:
            return label
    return BUCKET_EDGES[-1][0]


@dataclass(frozen=True, slots=True)
class Bucket:
    """One confidence band and how often it was right."""

    label: str
    count: int
    correct: int

    @property
    def precision(self) -> float:
        return self.correct / self.count if self.count else 0.0


@dataclass(frozen=True, slots=True)
class StrategyStats:
    """How one matching layer performed, on its own.

    Reported separately because the overall number can hide a broken layer: L1
    claiming certainty and getting one wrong would barely move a blended
    precision, while being the single most serious failure in the system (§4.1).
    """

    name: str
    attempted: int
    correct: int

    @property
    def precision(self) -> float:
        return self.correct / self.attempted if self.attempted else 0.0

    @property
    def label(self) -> str:
        """"L1_exact" -> "L1", so the layer reads the way the guide names it."""
        return self.name.split("_")[0]


@dataclass(frozen=True, slots=True)
class Metrics:
    """The marksheet for one run."""

    dataset: str
    seed: int
    scale: int
    no_llm: bool

    # -- coverage and accuracy -------------------------------------------
    total: int  # credits in the answer key
    attempted: int  # credits the agent claimed to explain
    correct: int  # of those, exactly right (exact set equality)
    match_rate: float
    match_precision: float

    # -- honesty ----------------------------------------------------------
    planted: int
    caught: int
    exception_recall: float
    missed: tuple[tuple[str, str], ...]  # (ref, reason_code) — reported by name
    false_positives: tuple[str, ...]  # claimed a credit the truth does not map

    # -- business value ---------------------------------------------------
    auto_posted: int
    auto_resolution: float

    # -- cost -------------------------------------------------------------
    llm_calls: int
    llm_cost_paise: int
    cost_per_100_paise: float

    # -- calibration ------------------------------------------------------
    calibration: tuple[Bucket, ...]
    by_strategy: tuple[StrategyStats, ...] = ()
    #: What L2 learned, and what the answer key actually planted (§4.2).
    inferred_fee_rate: float | None = None
    planted_fee_rate: float | None = None
    fee_model_summary: str = ""
    #: The §1.6 books-closed summary — the literal answer to the track title.
    books: dict[str, int] = field(default_factory=dict)

    # -- timing (wall-clock; excluded from the fingerprint) ---------------
    elapsed_s: float = 0.0
    throughput: float = 0.0
    layer_timings_ms: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Determinism
    # ------------------------------------------------------------------

    #: Fields derived from wall time. They vary run to run by nature, so they
    #: are excluded from the reproducibility fingerprint rather than pretended
    #: to be stable.
    TIMING_FIELDS = ("elapsed_s", "throughput", "layer_timings_ms")

    def deterministic_fields(self) -> dict[str, Any]:
        return {
            k: v for k, v in asdict(self).items() if k not in self.TIMING_FIELDS
        }

    @property
    def fingerprint(self) -> str:
        """Hash of every metric that is not wall-clock derived.

        §9.1 requires two runs to produce identical metrics. Throughput cannot
        be identical — it is a measurement of the machine, not of the system —
        so equality is asserted on this instead, which is both stronger and
        honest about what it covers.
        """
        blob = json.dumps(self.deterministic_fields(), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fingerprint"] = self.fingerprint
        return d


def pct(value: float) -> str:
    """Percent, truncated rather than rounded.

    §2.8 forbids rounding metrics up. 99.94% must not print as 100.0% — a
    suspicious 100 costs more credibility than an honest 99.9.
    """
    truncated = int(value * 1000) / 10
    return f"{truncated:.1f}%"


def render(m: Metrics, *, show_timing: bool = True) -> str:
    """The §7.4 marksheet."""
    rule = console_rule(60)
    dash = glyph("dash")
    out: list[str] = [
        f"RECONCILIATION REPORT {dash} {m.total} credits, {m.scale} orders, seed {m.seed}",
        rule,
        f"Match rate            {pct(m.match_rate):>7}   ({m.attempted}/{m.total})",
        f"Match precision       {pct(m.match_precision):>7}   "
        f"({m.correct}/{m.attempted} correct)   <- the number that matters",
        f"Exception recall      {pct(m.exception_recall):>7}   "
        f"({m.caught}/{m.planted} planted caught)",
        f"Auto-resolution       {pct(m.auto_resolution):>7}   "
        f"({m.auto_posted} posted without a human)",
    ]

    # Per layer, so a wrong confidence-1.00 match cannot hide inside a
    # blended number (§4.1).
    for s in m.by_strategy:
        coverage = s.attempted / m.total if m.total else 0.0
        out.append(
            f"{s.label} precision           {pct(s.precision):>7}   "
            f"({s.correct}/{s.attempted})   coverage {pct(coverage)}"
        )

    if show_timing:
        out.append(
            f"Throughput            {m.throughput:>7.1f} rec/sec   "
            f"(LLM calls: {m.llm_calls})"
        )
    else:
        out.append(f"LLM calls             {m.llm_calls:>7}")
    out.append(
        f"Cost per 100 records  {money_str(round(m.cost_per_100_paise)):>9}"
    )

    if m.inferred_fee_rate is not None and m.planted_fee_rate is not None:
        error = abs(m.inferred_fee_rate - m.planted_fee_rate)
        out += [
            rule,
            "Fee model (never configured, always inferred)",
            f"  inferred   {m.inferred_fee_rate:.4%}",
            f"  planted    {m.planted_fee_rate:.4%}   error {error:.2e}",
            f"  {m.fee_model_summary}",
        ]

    if m.books:
        b = m.books
        out += [
            rule,
            f"BOOKS CLOSED{'':<12}{'':>10}",
            f"  Auto-posted        {b['entries']:>6} entries   "
            f"{money_str(b['confirmed_in_bank']):>16}",
            f"  Pending review     {b['pending_review']:>6} entries   "
            f"{money_str(b['pending_review_paise']):>16}",
            f"  Exceptions         {b['exceptions']:>6} items     "
            f"{money_str(b['exceptions_paise']):>16}",
            "",
            f"  Revenue recognised {'':>6}           {money_str(b['revenue']):>16}",
            f"  Gateway fee expense{'':>6}           {money_str(b['fee_expense']):>16}",
            f"  GST input credit claimable         {money_str(b['gst_claimable']):>16}",
            f"  Rounding write-off                 {money_str(b['rounding_writeoff']):>16}",
            f"  {glyph('rule') * 50}",
            f"  Cash in bank (confirmed)           {money_str(b['confirmed_in_bank']):>16}",
            f"  Cash in transit                    {money_str(b['in_transit']):>16}",
            f"  In suspense                        {money_str(b['in_suspense']):>16}",
            f"    of which awaiting your approval   "
            f"{money_str(b['pending_review_paise']):>16}",
        ]

    out += [rule, "Confidence calibration"]
    if any(b.count for b in m.calibration):
        for b in m.calibration:
            marker = "  <- auto-post band" if b.label.startswith("0.95") else ""
            out.append(
                f"  {b.label:<12} {b.count:>5} records   "
                f"{pct(b.precision):>7} precision{marker}"
            )
    else:
        out.append("  (no matches attempted)")

    out.append(rule)
    if m.missed:
        out.append(f"Missed ({len(m.missed)}):")
        for ref, kind in m.missed[:10]:
            out.append(f"  {ref} {dash} {kind}")
        if len(m.missed) > 10:
            out.append(f"  ... and {len(m.missed) - 10} more")
    else:
        out.append("Missed (0): every planted exception was caught")

    if m.false_positives:
        out.append(
            f"False positives ({len(m.false_positives)}): "
            + ", ".join(m.false_positives[:5])
        )

    out += [rule, f"metrics fingerprint  {m.fingerprint}"]
    if m.no_llm:
        out.append("ablation             --no-llm (deterministic core only)")
    return "\n".join(out)


def render_multi_seed(runs: list[Metrics]) -> str:
    """Mean ± standard deviation across seeds. A single seed is an anecdote."""
    import statistics

    rule = console_rule(60)
    out = [f"MULTI-SEED REPORT {glyph('dash')} {len(runs)} seeds: "
           f"{', '.join(str(r.seed) for r in runs)}", rule]

    def line(name: str, values: list[float]) -> str:
        mean = statistics.fmean(values)
        sd = statistics.pstdev(values) if len(values) > 1 else 0.0
        return f"{name:<22}{pct(mean):>7}  {glyph('plusminus')} {pct(sd):>6}"

    out += [
        line("Match rate", [r.match_rate for r in runs]),
        line("Match precision", [r.match_precision for r in runs]),
        line("Exception recall", [r.exception_recall for r in runs]),
        line("Auto-resolution", [r.auto_resolution for r in runs]),
        rule,
    ]
    for r in runs:
        out.append(
            f"  seed {r.seed:<5} rate {pct(r.match_rate):>7}  "
            f"precision {pct(r.match_precision):>7}  {r.fingerprint}"
        )
    return "\n".join(out)
