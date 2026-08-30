"""The API the four screens consume. Guide §5.7, §8. Gate 12.

The rule this gate turns on: **every figure on every screen comes from an
endpoint.** These tests are the mechanical form of that — they check that the
numbers a screen would render are internally consistent, that they agree across
screens, and that they move when the seed moves.

The last one is the whole verification: anything that survives a seed change is
hardcoded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app

SEED_A = 42
SEED_B = 7

pytestmark = pytest.mark.skipif(
    not (Path("data") / f"seed{SEED_A}").exists()
    or not (Path("data") / f"seed{SEED_B}").exists(),
    reason="run `make generate` for seeds 42 and 7 first",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def exceptions(client: TestClient, seed: int) -> dict:
    r = client.get(f"/api/runs/current/exceptions?seed={seed}")
    assert r.status_code == 200, r.text
    return r.json()


def books(client: TestClient, seed: int) -> dict:
    r = client.get(f"/api/runs/current/books?seed={seed}")
    assert r.status_code == 200, r.text
    return r.json()


def review(client: TestClient, seed: int) -> dict:
    r = client.get(f"/api/runs/current/review?seed={seed}")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# The open balance column actually adds up
# ---------------------------------------------------------------------------


def test_the_open_balance_descends_to_exactly_zero(client: TestClient) -> None:
    """Frame 2a's third column is a running balance, not a progress bar. The
    amounts must sum to the header figure, or the column is decoration."""
    data = exceptions(client, SEED_A)
    assert data["balance_ties"], f"residual {data['residual_paise']} paise"
    assert data["residual_paise"] == 0

    rows = [e for e in data["exceptions"] if e["open_balance_paise"] is not None]
    assert rows
    assert rows[0]["open_balance_paise"] == data["unreconciled_paise"]

    running = data["unreconciled_paise"]
    for row in rows:
        assert row["open_balance_paise"] == running
        running -= row["amount_paise"] or 0
    assert running == 0


def test_every_row_carries_its_own_balance(client: TestClient) -> None:
    """Two rows showing the same balance is how this column first shipped: the
    map was keyed by ref, and one credit raised two findings."""
    data = exceptions(client, SEED_A)
    balances = [
        e["open_balance_paise"]
        for e in data["exceptions"]
        if e["open_balance_paise"] is not None
    ]
    assert len(balances) == len(set(balances)), "two rows share an open balance"


def test_one_credit_gets_one_card(client: TestClient) -> None:
    """Several checks can fire on one reference and each be true. Showing both
    counts one problem twice and, because the header sums the cards, overstates
    the money (§8.2, `core.reason_codes._PRECEDENCE`)."""
    data = exceptions(client, SEED_A)
    refs = [e["ref"] for e in data["exceptions"]]
    assert len(refs) == len(set(refs)), f"duplicate refs on the worklist: {refs}"


# ---------------------------------------------------------------------------
# In-transit is separate, and it ties
# ---------------------------------------------------------------------------


def test_in_transit_line_items_tie_to_the_total(client: TestClient) -> None:
    """A screen showing four rows under a total they do not reach is lying
    politely. The API says whether they tie; this asserts they do."""
    for seed in (SEED_A, SEED_B):
        data = exceptions(client, seed)
        transit = data["in_transit"]
        assert transit["ties"], (
            f"seed {seed}: items {transit['items_paise']} != "
            f"total {transit['total_paise']}"
        )
        assert transit["items_paise"] == sum(
            i["amount_paise"] or 0 for i in transit["items"]
        )


def test_in_transit_never_appears_in_the_exception_list(client: TestClient) -> None:
    """Appendix A: it is not a failure. Never in the list, never in the count."""
    data = exceptions(client, SEED_A)
    assert all(not e["in_transit"] for e in data["exceptions"])
    transit_refs = {i["ref"] for i in data["in_transit"]["items"]}
    assert transit_refs.isdisjoint({e["ref"] for e in data["exceptions"]})
    assert data["open"] == len(
        [e for e in data["exceptions"] if e["action_state"] is None]
    )


def test_in_transit_actions_post_nothing(client: TestClient) -> None:
    """Money on its way moves no entry. Every action offered on an in-transit
    row reports posts_entry false — read from the registry, not asserted here."""
    data = exceptions(client, SEED_A)
    for item in data["in_transit"]["items"]:
        assert item["actions"], f"{item['ref']} offers nothing to do"
        assert all(not a["posts_entry"] for a in item["actions"])


def test_in_transit_does_not_block_a_close(client: TestClient) -> None:
    """The close is held by unresolved exceptions, never by settlements in
    flight. Asserted through the refusal message, which names the reason."""
    data = exceptions(client, SEED_A)
    r = client.post(f"/api/runs/current/close?seed={SEED_A}")
    if data["open"]:
        assert r.status_code == 409
        assert "in-transit money does not block a close" in r.json()["detail"]


# ---------------------------------------------------------------------------
# The screens agree with each other
# ---------------------------------------------------------------------------


def test_review_and_books_quote_the_same_pending_figure(client: TestClient) -> None:
    """`total_debits` includes the fee and the GST; the cash awaiting approval
    does not. Summing the wrong one overstated the queue by Rs 6,944.22."""
    for seed in (SEED_A, SEED_B):
        r, b = review(client, seed), books(client, seed)
        assert r["total_paise"] == b["disposition"]["pending_review"]["paise"]
        assert r["count"] == b["disposition"]["pending_review"]["count"]


def test_exceptions_and_books_quote_the_same_exception_figure(
    client: TestClient,
) -> None:
    for seed in (SEED_A, SEED_B):
        e, b = exceptions(client, seed), books(client, seed)
        open_paise = e["unreconciled_paise"] + e["cleared_paise"]
        assert open_paise == b["disposition"]["exceptions"]["paise"]


def test_the_tie_out_ties(client: TestClient) -> None:
    """Four addends and a total, and the claim that they add up is computed —
    a tick drawn unconditionally is worth nothing (§1.6)."""
    for seed in (SEED_A, SEED_B):
        b = books(client, seed)
        assert b["tie_out"]["ties"], f"seed {seed} off by {b['tie_out']['delta_paise']}"
        assert sum(a["paise"] for a in b["tie_out"]["addends"]) == (
            b["tie_out"]["total"]["paise"]
        )


# ---------------------------------------------------------------------------
# Actions come from the registry, and only from the registry
# ---------------------------------------------------------------------------


def test_an_action_the_registry_does_not_offer_is_refused(
    client: TestClient,
) -> None:
    """§8.3: if the design offers an action the registry does not return for
    that reason code, the registry wins."""
    data = exceptions(client, SEED_A)
    card = data["exceptions"][0]
    offered = {a["code"] for a in card["actions"]}
    unavailable = next(
        c
        for c in ("IGNORE_DUPLICATE", "ACCEPT_WITH_WRITEOFF", "SNOOZE", "RERUN")
        if c not in offered
    )
    r = client.post(
        f"/api/exceptions/{card['ref']}/actions/{unavailable}?seed={SEED_A}"
    )
    assert r.status_code == 409
    assert "not available" in r.json()["detail"]


def test_every_card_offers_at_least_one_action(client: TestClient) -> None:
    """A card with no button is a report, not a worklist (§8.2)."""
    for seed in (SEED_A, SEED_B):
        for card in exceptions(client, seed)["exceptions"]:
            assert card["actions"], f"{card['ref']} ({card['reason_code']}) has none"


# ---------------------------------------------------------------------------
# WHAT / WHY / trace
# ---------------------------------------------------------------------------


def test_every_card_carries_what_and_why(client: TestClient) -> None:
    for seed in (SEED_A, SEED_B):
        for card in exceptions(client, seed)["exceptions"]:
            assert card["what"].strip(), f"{card['ref']} has no WHAT"
            assert card["why"].strip(), f"{card['ref']} has no WHY"
            assert card["why_source"] in {"model", "classifier"}


def test_the_trace_reconstructs_the_credit_it_explains(client: TestClient) -> None:
    """§8.5, and the reason the trace is built server-side: the arithmetic has
    to be checkable. An explained trail's steps must reach the credit."""
    from pipeline.factory import build_pipeline

    result = build_pipeline(no_llm=True).run(Path("data") / f"seed{SEED_A}")
    explained = [t for t in result.traces.values() if t.outcome == "explained"]
    assert explained
    for trace in explained:
        subtotal = next(s for s in trace.steps if s.kind == "subtotal")
        rounding = sum(s.signed_paise for s in trace.steps if s.kind == "residual")
        assert subtotal.signed_paise + rounding == trace.credit_paise, trace.ref


def test_a_bank_credit_card_carries_a_trace(client: TestClient) -> None:
    """Ledger-side findings (an order authorised but never captured) have no
    bank credit and so no trail. Every credit-shaped ref has one."""
    data = exceptions(client, SEED_A)
    for card in data["exceptions"]:
        if card["ref"].startswith("UTR-"):
            assert card["trace"] is not None, f"{card['ref']} has no trace"


# ---------------------------------------------------------------------------
# Review: balance is enforced in the handler, not by a greyed-out button
# ---------------------------------------------------------------------------


def test_every_prepared_entry_balances(client: TestClient) -> None:
    for seed in (SEED_A, SEED_B):
        for item in review(client, seed)["items"]:
            entry = item["prepared_entry"]
            assert entry["balanced"]
            assert entry["total_debits_paise"] == entry["total_credits_paise"]


def test_approving_twice_is_refused(client: TestClient) -> None:
    """Idempotency at the decision layer: the second press is a 409, not a
    second posting."""
    item = review(client, SEED_B)["items"][0]
    first = client.post(f"/api/review/{item['utr']}/approve?seed={SEED_B}")
    assert first.status_code == 200
    assert first.json()["entry_number"], "approval issued no journal number"
    second = client.post(f"/api/review/{item['utr']}/approve?seed={SEED_B}")
    assert second.status_code == 409


def test_an_unbalanced_entry_cannot_be_approved() -> None:
    """The handler refuses, so a caller bypassing the UI still cannot post it.
    Constructed directly because the pipeline cannot produce one — which is the
    point: the guard has to hold for an entry that should not exist."""
    from dataclasses import replace

    from api.routes.review import Unbalanced, approve
    from core.models import JournalLine

    class _Run:
        def __init__(self, item):
            self.review_decisions: dict[str, str] = {}
            self.result = type("R", (), {"review_queue": (item,)})()

    from pipeline.factory import build_pipeline

    real = build_pipeline(no_llm=True).run(Path("data") / f"seed{SEED_A}")
    item = real.review_queue[0]
    broken = replace(
        item,
        prepared_entry=replace(
            item.prepared_entry,
            lines=(*item.prepared_entry.lines, JournalLine("9999 Bogus", debit_paise=1)),
        ),
    )
    with pytest.raises(Unbalanced):
        approve(_Run(broken), broken.utr, "test")


# ---------------------------------------------------------------------------
# The gate-12 verification, mechanised
# ---------------------------------------------------------------------------


#: Figures that are IDENTICAL across seeds by construction, not by accident.
#: Each one is a property of the scale, the matcher or the chart of accounts —
#: never of the dataset's contents. Anything not on this list must move.
SEED_INVARIANT = {
    # `scale` fixes how many rows the generator writes; the seed fixes what is
    # in them. 500 orders is 605 records on any seed.
    "records_processed",
    # Booleans that are invariants, and would be bugs if they ever differed.
    "balance_ties",
    "in_transit_ties",
    "tie_out_ties",
}


def test_every_figure_changes_when_the_seed_changes(client: TestClient) -> None:
    """Start on seed 42, then on seed 7. Anything that survives is hardcoded."""
    a, b = exceptions(client, SEED_A), exceptions(client, SEED_B)
    ba, bb = books(client, SEED_A), books(client, SEED_B)
    ra, rb = review(client, SEED_A), review(client, SEED_B)

    moved = {
        "unreconciled_paise": (a["unreconciled_paise"], b["unreconciled_paise"]),
        "in_transit_total": (
            a["in_transit"]["total_paise"],
            b["in_transit"]["total_paise"],
        ),
        "in_transit_count": (a["in_transit"]["count"], b["in_transit"]["count"]),
        "top_ref": (a["exceptions"][0]["ref"], b["exceptions"][0]["ref"]),
        "review_total": (ra["total_paise"], rb["total_paise"]),
        "review_count": (ra["count"], rb["count"]),
        "auto_posted": (
            ba["disposition"]["auto_posted"]["paise"],
            bb["disposition"]["auto_posted"]["paise"],
        ),
        "revenue": (ba["tie_out"]["total"]["paise"], bb["tie_out"]["total"]["paise"]),
        "gateway_fee": (
            ba["tie_out"]["addends"][1]["paise"],
            bb["tie_out"]["addends"][1]["paise"],
        ),
        "suspense": (ba["suspense"]["paise"], bb["suspense"]["paise"]),
    }
    stuck = [name for name, (x, y) in moved.items() if x == y]
    assert not stuck, f"identical on both seeds, so hardcoded: {stuck}"


def test_the_fee_model_is_inferred_separately_on_each_seed(
    client: TestClient,
) -> None:
    """Both seeds plant the same MDR, so the RATE is legitimately near-identical.
    What must differ is the inference: the two runs learn it from different
    settlements and land on different floating-point values."""
    a = client.get(f"/api/runs/current?seed={SEED_A}").json()
    b = client.get(f"/api/runs/current?seed={SEED_B}").json()
    assert a["fee_rate"] != b["fee_rate"], "the rate was not inferred per run"
    assert abs(a["fee_rate"] - b["fee_rate"]) < 1e-5, "and yet they should agree"


def test_a_rejection_carries_the_reviewer_s_reason_code(client: TestClient) -> None:
    """A rejected entry reappears on /exceptions, and it should arrive saying
    WHY a person declined it.

    Without the code the row lands back on the worklist carrying only "a human
    said no", which throws away the most useful thing the human knew and makes
    the exception list unfilterable by the codes it is filtered by everywhere
    else.
    """
    queue = client.get(f"/api/runs/current/review?seed={SEED_B}").json()
    if not queue["items"]:
        pytest.skip("seed has an empty review queue")
    utr = queue["items"][0]["utr"]

    r = client.post(
        f"/api/review/{utr}/reject?seed={SEED_B}&reason_code=DUPLICATE_UTR"
    )
    assert r.status_code == 200, r.text
    assert r.json()["reason_code"] == "DUPLICATE_UTR"

    trail = client.get(f"/api/runs/current/audit-trail?seed={SEED_B}").json()
    rejected = [e for e in trail["events"] if e["subject"] == utr]
    assert rejected, "the rejection was not recorded in the audit trail"
    assert "DUPLICATE_UTR" in rejected[-1]["detail"]


def test_a_rejection_may_not_invent_a_reason_code(client: TestClient) -> None:
    """An exception list filtered by codes reviewers made up is not filterable.

    422 rather than 400: the request was well-formed, the code simply is not
    one the system defines, and the reviewer needs to be shown the list.
    """
    queue = client.get(f"/api/runs/current/review?seed={SEED_A}").json()
    if not queue["items"]:
        pytest.skip("seed has an empty review queue")
    utr = queue["items"][0]["utr"]

    r = client.post(
        f"/api/review/{utr}/reject?seed={SEED_A}&reason_code=LOOKS_WRONG_TO_ME"
    )
    assert r.status_code == 422, r.text
    assert "LOOKS_WRONG_TO_ME" in r.text

    # And the entry is still undecided — a refused rejection must not half-apply.
    after = client.get(f"/api/runs/current/review?seed={SEED_A}").json()
    assert any(i["utr"] == utr for i in after["items"])


def test_the_nav_counts_agree_with_the_screens_they_point_at(
    client: TestClient,
) -> None:
    """A tab that says 11 over a page showing 10 is worse than a tab with no
    number on it.

    Both counts move when a decision is made: the run's own `pending_review`
    is what the PIPELINE produced and never changes, so a nav built on it goes
    stale the moment anybody approves anything.
    """
    before = client.get(f"/api/runs/current?seed={SEED_A}").json()
    queue = client.get(f"/api/runs/current/review?seed={SEED_A}").json()
    assert before["pending_review"] == queue["count"], (
        "the nav and /review disagree before anything was decided"
    )

    exc = client.get(f"/api/runs/current/exceptions?seed={SEED_A}").json()
    assert before["open_exceptions"] == exc["open"], (
        "the nav and /exceptions disagree before anything was acted on"
    )

    if not queue["items"]:
        pytest.skip("seed has an empty review queue")
    utr = queue["items"][0]["utr"]
    assert client.post(f"/api/review/{utr}/approve?seed={SEED_A}").status_code == 200

    after = client.get(f"/api/runs/current?seed={SEED_A}").json()
    after_queue = client.get(f"/api/runs/current/review?seed={SEED_A}").json()
    assert after["pending_review"] == after_queue["count"] == queue["count"] - 1


@pytest.mark.parametrize("seed", [SEED_A, SEED_B])
def test_calibration_accounts_for_every_record(client: TestClient, seed: int) -> None:
    """The table is captioned "every record grouped by how sure the system was",
    so it has to hold every record.

    The buckets are keyed on a confidence, and a credit the system DECLINED to
    answer has none — so it fell through all of them. The bars summed to 59 of
    60 and the one record this page exists to be honest about was the one it
    quietly dropped.
    """
    m = client.post(f"/api/benchmark?seed={seed}").json()
    held = sum(b["records"] for b in m["calibration"])
    assert held == m["total"], (
        f"calibration holds {held} of {m['total']} records — "
        f"{m['total'] - held} vanished"
    )

    declined = [b for b in m["calibration"] if b["declined"]]
    assert len(declined) == 1, "there should be exactly one declined row"
    assert declined[0]["records"] == m["total"] - m["attempted"]
    # Declining is not an answer, so it cannot be scored right or wrong.
    assert declined[0]["correct"] == 0

    scored = [b for b in m["calibration"] if not b["declined"]]
    assert sum(b["records"] for b in scored) == m["attempted"]


def test_books_report_the_ledger_now_not_the_pipeline_snapshot(
    client: TestClient,
) -> None:
    """Approving an entry posts a real journal entry. /books has to see it.

    `result.cash_position` is where the PIPELINE left things, before anybody
    touched anything. Reading it meant the screen whose headline is "every
    rupee that moved is on one of three lines, and the three lines add up"
    kept reporting a state that stopped being true the moment a controller did
    their job: the audit trail's "approved in review" ticked up while revenue,
    suspense and the rounding write-off stayed frozen.
    """
    seed = SEED_B

    def books() -> dict:
        return client.get(f"/api/runs/current/books?seed={seed}").json()

    def rounding(b: dict) -> int:
        return next(a["paise"] for a in b["tie_out"]["addends"] if "ound" in a["label"])

    queue = client.get(f"/api/runs/current/review?seed={seed}").json()
    carrying = [
        i
        for i in queue["items"]
        if any(
            "Rounding" in ln["account"] and ln["debit_paise"]
            for ln in i["prepared_entry"]["lines"]
        )
    ]
    if not carrying:
        pytest.skip("no queued entry carries a rounding line on this seed")
    item = carrying[0]
    drift = next(
        ln["debit_paise"]
        for ln in item["prepared_entry"]["lines"]
        if "Rounding" in ln["account"]
    )

    before = books()
    r = client.post(f"/api/review/{item['utr']}/approve?seed={seed}")
    assert r.status_code == 200, r.text
    assert r.json()["posted"] is True
    after = books()

    # The money left the queue and left suspense, by exactly its bank amount.
    bank = item["amount_paise"]
    assert (
        before["disposition"]["pending_review"]["paise"]
        - after["disposition"]["pending_review"]["paise"]
        == bank
    )
    assert before["suspense"]["paise"] - after["suspense"]["paise"] == bank

    # The entry's own rounding line reached the books.
    assert rounding(after) - rounding(before) == drift

    # Revenue rose by what the entry credited to receivables.
    ar = next(
        ln["credit_paise"]
        for ln in item["prepared_entry"]["lines"]
        if "Receivable" in ln["account"]
    )
    assert (
        after["tie_out"]["total"]["paise"] - before["tie_out"]["total"]["paise"] == ar
    )

    # And after all that the tie-out still holds.
    assert after["tie_out"]["ties"], "the books stopped tying after an approval"
    assert (
        sum(a["paise"] for a in after["tie_out"]["addends"])
        == after["tie_out"]["total"]["paise"]
    )


@pytest.mark.parametrize("seed", [SEED_A, SEED_B])
def test_benchmark_reports_the_thresholds_its_bands_are_labelled_from(
    client: TestClient, seed: int
) -> None:
    """The calibration chart labels each band with where that confidence would
    be ROUTED, so it has to be told the policy.

    It was not: the payload carried no thresholds at all, so the frontend
    hardcoded 0.95 and 0.85 and labelled the 0.70-0.85 band "exception" —
    while the run's review floor is 0.70, meaning anything in that band goes
    to a person. Change the policy and the labels have to follow it.
    """
    from core.config import Settings

    m = client.post(f"/api/benchmark?seed={seed}").json()
    settings = Settings()
    assert m["auto_post_threshold"] == settings.auto_post_threshold
    assert m["review_threshold"] == settings.review_threshold

    # Band edges and routing thresholds are different things, and only two of
    # the four coincide. Both policy edges must be band edges, or a band would
    # straddle a routing boundary and no single label could be right.
    # "0.95 - 1.00" and "below 0.70" — take every number in every label.
    edges = {
        float(tok)
        for b in m["calibration"]
        if not b["declined"]
        for tok in re.findall(r"\d+\.\d+", b["label"])
    }
    assert settings.auto_post_threshold in edges
    assert settings.review_threshold in edges
