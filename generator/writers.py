"""CSV and truth.json emission. Guide §6.2 steps 4-5.

    ledger.csv       order_id, amount, capture_date, status
    settlement.csv   settlement_id, utr, settle_date, gross, fee, gst, net, order_id
    bank.csv         value_date, amount, type, narration, utr

THE SINGLE MOST IMPORTANT RULE IN THE GENERATOR:
bank.csv narration must be realistic gateway noise like "NEFT RAZORPAYSETL88 CR"
and must NEVER contain an order ID. Leaking them makes the problem trivially
solvable and every accuracy number reported meaningless. `assert_no_order_ids`
below enforces it at write time; the standing drift check enforces it again on
the file that lands on disk.

Everything here is written with an explicit "\\n" terminator and UTF-8, so a run
on Windows is byte-identical to a run on Linux — the determinism guarantee in
§2.7 rule 2 is a checksum claim, and CRLF would silently break it.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from generator.world import Batch, OrderStatus, World

GENERATOR_VERSION = "1.0.0"

#: Realistic settlement narrations. Each embeds the settlement number — a real
#: signal L4 can reason about (§4.4 job A) — and nothing else identifying.
NARRATION_FORMATS = (
    "NEFT RAZORPAYSETL{n} CR",
    "NEFT-RAZORPAY-SETL{n}-CR",
    "IMPS/RAZORPAY/SETL{n}",
    "RTGS RAZORPAY SETTLEMENT {n}",
    "NEFT RZPSETL{n} COLLECTIONS CR",
)


def rupees(paise: int) -> str:
    """Plain decimal string for a CSV cell. No symbol, no grouping.

    Formatted from integers so no float touches a monetary value even here.
    """
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    return f"{sign}{whole}.{frac:02d}"


def narration_for(batch: Batch) -> str:
    """Bank narration. Contains the settlement number and NEVER an order ID."""
    number = batch.settlement_id.split("-")[-1]
    fmt = NARRATION_FORMATS[int(number) % len(NARRATION_FORMATS)]
    return fmt.format(n=number)


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


# --------------------------------------------------------------------------
# ledger.csv — the merchant's own books: what SHOULD have happened
# --------------------------------------------------------------------------


def write_ledger(path: Path, world: World) -> None:
    hidden = {
        oid
        for b in world.batches
        if b.orders_hidden_from_ledger
        for oid in b.order_ids
    }
    rows: list[Sequence[Any]] = []
    for oid in sorted(world.orders):
        if oid in hidden:  # MISSING_IN_LEDGER: the books never recorded this
            continue
        o = world.orders[oid]
        rows.append([o.order_id, rupees(o.amount_paise), o.capture_date.isoformat(), o.status])
    for rid in sorted(world.refunds):
        r = world.refunds[rid]
        # Refunds are negative amounts — the same sign convention the matcher
        # uses downstream, so they need no special case (§4.3a).
        rows.append(
            [r.refund_id, rupees(-r.amount_paise), r.refund_date.isoformat(), OrderStatus.REFUND]
        )
    rows.sort(key=lambda r: (str(r[2]), str(r[0])))
    _write_csv(path, ("order_id", "amount", "capture_date", "status"), rows)


# --------------------------------------------------------------------------
# settlement.csv — the bridge document, the only file carrying both keys
# --------------------------------------------------------------------------


def write_settlement(path: Path, world: World) -> None:
    rows: list[Sequence[Any]] = []
    for batch in sorted(world.batches, key=lambda b: (b.settle_date, b.settlement_id)):
        if not batch.order_ids:
            continue
        totals = (
            rupees(batch.gross(world)),
            rupees(batch.fee(world)),
            rupees(batch.gst(world)),
            rupees(batch.net(world)),
        )
        for oid in sorted(batch.order_ids):
            rows.append(
                [
                    batch.settlement_id,
                    batch.utr,
                    batch.settle_date.isoformat(),
                    *totals,
                    oid,
                ]
            )
        # Cross-period refunds are deliberately NOT itemised here. They are
        # deducted from `net` but absent from the order rows, which is what
        # makes the batch total unexplainable without a wider search (§4.3b).
    _write_csv(
        path,
        ("settlement_id", "utr", "settle_date", "gross", "fee", "gst", "net", "order_id"),
        rows,
    )


# --------------------------------------------------------------------------
# bank.csv — terse, context-free, one line per credit
# --------------------------------------------------------------------------


def write_bank(path: Path, world: World) -> None:
    rows: list[Sequence[Any]] = []
    for batch in sorted(world.batches, key=lambda b: (b.settle_date, b.settlement_id)):
        if not batch.order_ids:
            continue
        row = [
            batch.settle_date.isoformat(),
            rupees(batch.net(world)),
            "CR",
            narration_for(batch),
            batch.utr,
        ]
        rows.append(row)
        if batch.utr in world.duplicated_utrs:
            rows.append(list(row))  # DUPLICATE_UTR: the same credit, twice
    assert_no_order_ids(rows)
    _write_csv(path, ("value_date", "amount", "type", "narration", "utr"), rows)


def assert_no_order_ids(rows: Iterable[Sequence[Any]]) -> None:
    """The check that invalidates everything if it fails (§6.2 step 4).

    Enforced here rather than only in review, because a leak makes every
    accuracy number meaningless and would not otherwise surface until someone
    noticed L1 coverage above 90%.
    """
    for row in rows:
        blob = " ".join(str(cell) for cell in row).upper()
        for token in ("ORD-", "ORD_", "RFND-"):
            if token in blob:
                raise AssertionError(
                    f"order id leaked into bank.csv: {row!r} — this makes the "
                    "problem trivial and every reported metric meaningless"
                )


# --------------------------------------------------------------------------
# truth.json — the answer key. Read only by the eval harness, never the agent.
# --------------------------------------------------------------------------


def write_truth(path: Path, world: World) -> dict[str, Any]:
    truth: dict[str, Any] = {
        "generator_version": GENERATOR_VERSION,
        "seed": world.seed,
        "scale": world.scale,
        # Deliberately the dataset's own period end, not the wall clock: a
        # timestamp would make two runs of the same seed differ, breaking the
        # byte-identical guarantee (§2.7 rule 2) and §9.2.
        "generated_at": world.period.end.isoformat(),
        "fee_rate": world.fee_rate,
        "gst_rate": world.gst_rate,
        # Every distinct MDR present, so a multi-slab dataset can be scored
        # against what was actually planted rather than against one headline
        # rate (§4.2 step 3).
        "fee_slabs": sorted(
            {world.fee_rate}
            | {b.fee_rate_override for b in world.batches if b.fee_rate_override}
        ),
        "period": {
            "start": world.period.start.isoformat(),
            "end": world.period.end.isoformat(),
        },
        "mappings": {utr: members for utr, members in sorted(world.mappings().items())},
        "exceptions": [
            {"ref": e.ref, "type": str(e.type)}
            for e in sorted(world.exceptions, key=lambda e: (str(e.type), e.ref))
        ],
    }
    path.write_text(
        json.dumps(truth, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return truth
