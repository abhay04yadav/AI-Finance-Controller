"""ledger.csv -> Records. Guide §4.0.

    order_id, amount, capture_date, status

The merchant's own books: what SHOULD have happened (§1.2). Refund lines carry a
negative amount and become OUTFLOW records, so they enter the L3 candidate pool
as negative numbers through the same code path as a sale (§4.3a).
"""

from __future__ import annotations

from pathlib import Path

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

COLUMNS = ("order_id", "amount", "capture_date", "status")


class LedgerAdapter:
    source = Source.LEDGER
    filename = "ledger.csv"

    def load(self, path: Path) -> IngestResult:
        records: list[Record] = []
        failures: list[IngestFailure] = []

        try:
            require_columns(path, COLUMNS)
        except IngestError as exc:
            return IngestResult(failures=(IngestFailure(self.source, 1, str(exc)),))

        for line_no, row in read_rows(path):
            try:
                require(row, "order_id", "amount", "capture_date")
                amount = parse_amount(row["amount"])
                records.append(
                    Record(
                        source=self.source,
                        external_id=row["order_id"].strip(),
                        # The sign lives in `direction`, so the amount itself is
                        # always the magnitude. `signed_amount` puts it back.
                        amount=abs(amount),
                        value_date=parse_business_date(row["capture_date"]),
                        direction=(
                            Direction.OUTFLOW if amount.paise < 0 else Direction.INFLOW
                        ),
                        narration=row.get("status", "").strip(),
                        refs=extract_refs(row["order_id"]),
                        raw=dict(row),
                    )
                )
            except IngestError as exc:
                # Never skipped, never repaired: it surfaces as INGEST_ERROR.
                failures.append(IngestFailure(self.source, line_no, str(exc), dict(row)))

        return IngestResult(tuple(records), tuple(failures))
