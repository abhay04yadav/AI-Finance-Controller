"""settlement.csv -> Records. Guide §4.0, §4.1.

    settlement_id, utr, settle_date, gross, fee, gst, net, order_id

The settlement report is **the bridge document** (§1.2): the only file carrying
both `order_id` and `utr`, which is what makes L1's two-hop join possible.

    ledger.order_id ──► settlement.order_id
                        settlement.utr ──► bank.utr

**This adapter aggregates.** The file is long-format — one row per order, with
the batch's gross/fee/gst/net repeated on every row. Emitting one Record per row
would give each a meaningless `amount` (the batch net, N times over), so source
totals could never tie to the file. Instead each *settlement* becomes one Record:

    external_id  the settlement_id            (unique, so the §4.0 step 5
                                               duplicate rule holds naturally)
    amount       the net actually paid out
    refs         {settlement_id, utr, every order_id in the batch}
    raw          the batch totals plus the ordered member list

That is precisely what an Adapter is for: the quirk dies at the boundary and no
layer downstream ever learns that this file repeats itself (§5.3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models import Direction, Record, Source
from ingest.normalizer import (
    IngestError,
    IngestFailure,
    IngestResult,
    extract_refs,
    parse_amount,
    parse_business_date,
    read_rows,
    require,
    require_columns,
)

#: One in-progress settlement while its rows are being folded together.
Batch = dict[str, Any]

COLUMNS = (
    "settlement_id",
    "utr",
    "settle_date",
    "gross",
    "fee",
    "gst",
    "net",
    "order_id",
)


def _assert_consistent(settlement_id: str, batch: Batch, row: dict[str, str]) -> None:
    """Every row of a settlement must agree about that settlement.

    A file that contradicts itself is corrupt, not merely untidy. Repairing
    it silently — taking the first net, say — would post a wrong entry.
    """
    if parse_amount(row["net"]) != batch["net"]:
        raise IngestError(
            f"settlement {settlement_id} reports two different nets "
            f"({batch['net']} and {row['net']})"
        )
    if row["utr"].strip() != batch["utr"]:
        raise IngestError(
            f"settlement {settlement_id} reports two different UTRs "
            f"({batch['utr']} and {row['utr']})"
        )


class SettlementAdapter:
    source = Source.SETTLEMENT
    filename = "settlement.csv"

    def load(self, path: Path) -> IngestResult:
        failures: list[IngestFailure] = []

        try:
            require_columns(path, COLUMNS)
        except IngestError as exc:
            return IngestResult(failures=(IngestFailure(self.source, 1, str(exc)),))

        batches: dict[str, Batch] = {}

        for line_no, row in read_rows(path):
            try:
                require(row, "settlement_id", "utr", "settle_date", "net", "order_id")
                settlement_id = row["settlement_id"].strip()
                batch = batches.get(settlement_id)
                if batch is None:
                    batch = {
                        "settlement_id": settlement_id,
                        "utr": row["utr"].strip(),
                        "settle_date": parse_business_date(row["settle_date"]),
                        "gross": parse_amount(row["gross"]),
                        "fee": parse_amount(row["fee"]),
                        "gst": parse_amount(row["gst"]),
                        "net": parse_amount(row["net"]),
                        "order_ids": [],
                        "first_line": line_no,
                    }
                    batches[settlement_id] = batch
                else:
                    _assert_consistent(settlement_id, batch, row)
                batch["order_ids"].append(row["order_id"].strip())
            except IngestError as exc:
                failures.append(IngestFailure(self.source, line_no, str(exc), dict(row)))

        records: list[Record] = []
        for settlement_id in sorted(batches):
            batch = batches[settlement_id]
            members = sorted(batch["order_ids"])
            records.append(
                Record(
                    source=self.source,
                    external_id=settlement_id,
                    amount=abs(batch["net"]),
                    value_date=batch["settle_date"],
                    direction=(
                        Direction.OUTFLOW
                        if batch["net"].paise < 0
                        else Direction.INFLOW
                    ),
                    narration=f"{settlement_id} / {batch['utr']}",
                    refs=extract_refs(settlement_id, batch["utr"], *members),
                    raw={
                        "settlement_id": settlement_id,
                        "utr": batch["utr"],
                        "settle_date": batch["settle_date"].isoformat(),
                        "gross_paise": batch["gross"].paise,
                        "fee_paise": batch["fee"].paise,
                        "gst_paise": batch["gst"].paise,
                        "net_paise": batch["net"].paise,
                        "order_ids": members,
                    },
                )
            )

        return IngestResult(tuple(records), tuple(failures))
