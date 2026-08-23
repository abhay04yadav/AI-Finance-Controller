"""Self-validation. Guide §6.3 "Self-validating".

Runs after the files are written and reads them back off disk, not from memory.
A generator bug that produces an unsolvable dataset looks *identical* to a
matcher bug, and would be diagnosed at gate 5 or 7 after hours of looking in the
wrong place. These assertions make it fail here instead, loudly.

Reading back from disk rather than trusting the in-memory world is the point: it
catches writer bugs, not just model bugs.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from core.money import Money
from generator.injectors import build_registry


class GeneratorInvariantError(AssertionError):
    """The dataset it just wrote is internally inconsistent. Always a bug."""


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def validate(out_dir: Path) -> dict[str, int]:
    """Assert the written dataset is internally consistent and solvable."""
    ledger = _read(out_dir / "ledger.csv")
    settlement = _read(out_dir / "settlement.csv")
    bank = _read(out_dir / "bank.csv")
    truth = json.loads((out_dir / "truth.json").read_text(encoding="utf-8"))

    ledger_ids = {r["order_id"] for r in ledger}
    bank_utrs = [r["utr"] for r in bank]

    def check(condition: bool, message: str) -> None:
        if not condition:
            raise GeneratorInvariantError(message)

    # -- 1. The answer key resolves ---------------------------------------
    for utr, members in truth["mappings"].items():
        check(utr in bank_utrs, f"truth maps {utr} but no such bank credit exists")
        check(bool(members), f"truth maps {utr} to an empty member list")
        for ref in members:
            check(
                ref in ledger_ids,
                f"truth maps {utr} to {ref}, which is absent from ledger.csv",
            )

    # -- 2. Every planted exception is actually present --------------------
    for exc in truth["exceptions"]:
        ref, kind = exc["ref"], exc["type"]
        if ref.startswith("UTR-"):
            check(ref in bank_utrs, f"{kind} planted on {ref}, absent from bank.csv")
        else:
            present = ref in ledger_ids
            # MISSING_IN_LEDGER is the one mode whose whole point is absence.
            check(
                present or kind == "MISSING_IN_LEDGER",
                f"{kind} planted on {ref}, absent from ledger.csv",
            )

    # -- 3. Money ties, to the paise ---------------------------------------
    for row in settlement:
        gross = Money.from_rupee_string(row["gross"]).paise
        fee = Money.from_rupee_string(row["fee"]).paise
        gst = Money.from_rupee_string(row["gst"]).paise
        net = Money.from_rupee_string(row["net"]).paise
        check(
            net <= gross - fee - gst,
            f"settlement {row['settlement_id']} nets more than gross less charges",
        )

    by_utr = {r["utr"]: r for r in settlement}
    for row in bank:
        if row["utr"] not in by_utr:
            continue
        check(
            Money.from_rupee_string(row["amount"]).paise
            == Money.from_rupee_string(by_utr[row["utr"]]["net"]).paise,
            f"bank credit for {row['utr']} disagrees with the settlement net",
        )

    # -- 4. Nothing leaked into the bank narration -------------------------
    blob = (out_dir / "bank.csv").read_text(encoding="utf-8").upper()
    for token in ("ORD-", "RFND-"):
        check(
            token not in blob,
            f"{token} appears in bank.csv — the problem is now trivial and every "
            "reported metric is meaningless",
        )

    # -- 5. Duplicates are real duplicates ---------------------------------
    planted_dupes = {
        e["ref"] for e in truth["exceptions"] if e["type"] == "DUPLICATE_UTR"
    }
    for utr in planted_dupes:
        check(
            bank_utrs.count(utr) == 2,
            f"DUPLICATE_UTR planted on {utr} but it appears "
            f"{bank_utrs.count(utr)}x in bank.csv",
        )
    for utr in set(bank_utrs):
        check(
            bank_utrs.count(utr) == 1 or utr in planted_dupes,
            f"{utr} is duplicated in bank.csv but was never planted as one",
        )

    # -- 6. The dataset is actually solvable -------------------------------
    check(
        len(truth["mappings"]) > 0,
        "no mappings at all — nothing for the agent to find",
    )
    check(
        len(truth["exceptions"]) > 0,
        "no planted exceptions — exception recall would be undefined",
    )

    # -- 7. Every failure mode is actually exercised -----------------------
    # Without this the generator will happily emit a dataset missing half its
    # failure modes and still report "self-validation passed" — which is exactly
    # what happened when batch-level injectors starved at scale 5000.
    planted_types = {e["type"] for e in truth["exceptions"]}
    expected_types = {str(i.reason_code) for i in build_registry()}
    check(
        planted_types == expected_types,
        f"failure modes missing from the dataset: "
        f"{sorted(expected_types - planted_types)} — exception recall computed "
        f"over this dataset would silently ignore them",
    )

    return {
        "ledger_rows": len(ledger),
        "settlement_rows": len(settlement),
        "bank_rows": len(bank),
        "mappings": len(truth["mappings"]),
        "exceptions": len(truth["exceptions"]),
    }
