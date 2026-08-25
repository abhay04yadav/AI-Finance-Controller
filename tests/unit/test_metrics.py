"""Metric rendering and honesty rules. Guide §7.4, §2.8."""

from __future__ import annotations

import pytest

from eval.metrics import BUCKET_EDGES, Bucket, Metrics, bucket_of, pct, render


def metrics(**over: object) -> Metrics:
    base: dict[str, object] = dict(
        dataset="data/seed42",
        seed=42,
        scale=500,
        no_llm=False,
        total=500,
        attempted=482,
        correct=481,
        match_rate=482 / 500,
        match_precision=481 / 482,
        planted=18,
        caught=17,
        exception_recall=17 / 18,
        missed=(("ORD-4471", "ROUNDING_DRIFT"),),
        resolvable_planted=17,
        resolvable_resolved=16,
        must_surface_planted=10,
        must_surface_flagged=10,
        genuine_misses=(("ORD-4471", "ROUNDING_DRIFT"),),
        false_positives=(),
        auto_posted=441,
        auto_resolution=441 / 500,
        llm_calls=23,
        llm_cost_paise=155,
        cost_per_100_paise=31.0,
        calibration=(
            Bucket("0.95 - 1.00", 441, 441),
            Bucket("0.85 - 0.95", 28, 27),
            Bucket("0.70 - 0.85", 13, 11),
            Bucket("below 0.70", 8, 3),
        ),
        elapsed_s=1.2,
        throughput=412.0,
    )
    base.update(over)
    return Metrics(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Buckets
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "label"),
    [
        (1.00, "0.95 - 1.00"),
        (0.95, "0.95 - 1.00"),  # inclusive lower edge = the auto-post threshold
        (0.949, "0.85 - 0.95"),
        (0.85, "0.85 - 0.95"),
        (0.84, "0.70 - 0.85"),
        (0.70, "0.70 - 0.85"),
        (0.69, "below 0.70"),
        (0.00, "below 0.70"),
    ],
)
def test_bucket_boundaries(confidence: float, label: str) -> None:
    assert bucket_of(confidence) == label


def test_buckets_are_exhaustive_and_disjoint() -> None:
    for i in range(0, 101):
        assert bucket_of(i / 100) in {label for label, _, _ in BUCKET_EDGES}


def test_empty_bucket_precision_is_zero_not_a_crash() -> None:
    assert Bucket("x", 0, 0).precision == 0.0


def test_an_empty_bucket_does_not_render_as_zero_precision() -> None:
    """0 records at 0.0% precision reads as "everything here was wrong" rather
    than "nothing landed here"."""
    m = metrics(
        calibration=(
            Bucket("0.95 - 1.00", 48, 48),
            Bucket("0.85 - 0.95", 11, 11),
            Bucket("0.70 - 0.85", 0, 0),
            Bucket("below 0.70", 0, 0),
        )
    )
    lines = [line for line in render(m).splitlines() if "0.70 - 0.85" in line]
    assert lines
    assert "0.0%" not in lines[0], lines[0]


# --------------------------------------------------------------------------
# Honesty: §2.8 forbids rounding metrics up
# --------------------------------------------------------------------------


def test_percentages_truncate_rather_than_round_up() -> None:
    """99.94% must not print as 100.0%. A suspicious 100 costs more credibility
    than an honest 99.9."""
    assert pct(0.9994) == "99.9%"
    assert pct(0.99999) == "99.9%"
    assert pct(1.0) == "100.0%"
    assert pct(0.0) == "0.0%"


def test_report_shows_both_rate_and_precision() -> None:
    out = render(metrics())
    assert "Match rate" in out
    assert "Match precision" in out
    assert "the number that matters" in out


def test_report_names_its_own_misses() -> None:
    """The self-reported miss is deliberate and worth more than a clean 100%."""
    out = render(metrics())
    assert "Missed (1)" in out
    assert "ORD-4471" in out
    assert "ROUNDING_DRIFT" in out


def test_the_miss_list_counts_only_genuine_misses() -> None:
    """A planted anomaly the matcher RESOLVED is not a miss.

    The old list counted every planted anomaly that was not flagged, which
    reported 16 failures where the honest number was 1 — and reported our own
    successes as shortfalls in front of a judge.
    """
    m = metrics(
        resolvable_planted=17,
        resolvable_resolved=17,
        must_surface_planted=10,
        must_surface_flagged=10,
        genuine_misses=(),
        missed=tuple((f"ORD-{i}", "HOLIDAY_SHIFT") for i in range(16)),
    )
    out = render(m)
    assert "Missed (0)" in out, "resolved anomalies are being listed as misses"
    assert "resolved or surfaced" in out


def test_the_two_rates_are_reported_apart() -> None:
    """Absorbing a hard case and surfacing an unresolvable one are different
    jobs, and one number cannot score both."""
    out = render(metrics())
    assert "Anomaly resolution" in out
    assert "Exception recall" in out
    assert "Genuine misses" in out


def test_report_says_so_when_nothing_was_missed() -> None:
    out = render(metrics(missed=(), genuine_misses=(), caught=18,
                         exception_recall=1.0))
    assert "Missed (0)" in out


def test_report_shows_all_seven_metrics() -> None:
    out = render(metrics())
    for label in (
        "Match rate",
        "Match precision",
        "Exception recall",
        "Auto-resolution",
        "Throughput",
        "Cost per 100 records",
        "Confidence calibration",
    ):
        assert label in out, f"missing from the marksheet: {label}"


def test_report_marks_the_auto_post_band() -> None:
    assert "auto-post band" in render(metrics())


def test_report_flags_an_ablation_run() -> None:
    assert "--no-llm" in render(metrics(no_llm=True))
    assert "--no-llm" not in render(metrics(no_llm=False))


def test_report_surfaces_false_positives() -> None:
    out = render(metrics(false_positives=("UTR-999",)))
    assert "False positives" in out
    assert "UTR-999" in out


# --------------------------------------------------------------------------
# Determinism of the printed report
# --------------------------------------------------------------------------


def test_no_timing_makes_two_reports_byte_identical() -> None:
    """§9.1 asks for two runs to produce identical metrics. Throughput measures
    the machine, not the system, so `--no-timing` removes it and the rest of the
    report diffs cleanly."""
    a = render(metrics(elapsed_s=1.2, throughput=412.0), show_timing=False)
    b = render(metrics(elapsed_s=3.7, throughput=139.0), show_timing=False)
    assert a == b


def test_timing_lines_do_differ_when_shown() -> None:
    a = render(metrics(throughput=412.0), show_timing=True)
    b = render(metrics(throughput=139.0), show_timing=True)
    assert a != b


def test_fingerprint_is_printed() -> None:
    m = metrics()
    assert m.fingerprint in render(m)


def test_fingerprint_excludes_timing() -> None:
    assert "elapsed_s" not in metrics().deterministic_fields()
    assert "throughput" not in metrics().deterministic_fields()


def test_to_dict_is_json_serialisable() -> None:
    import json

    json.dumps(metrics().to_dict(), default=str)
