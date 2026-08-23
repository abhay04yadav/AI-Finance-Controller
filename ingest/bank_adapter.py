"""bank.csv -> Records. Guide §4.0.

    value_date, amount, type, narration, utr

The bank statement is context-free (§1.4 reason 5): terse, one line per credit,
and it never carries an order ID. `refs` is populated by tokenizing the narration
so the matcher can attempt joins without knowing which column held the key.

Duplicate UTRs are NOT an ingest fault. The same credit appearing twice is a real
signal (DUPLICATE_UTR), and collapsing it here would let L1 double-post revenue,
so both rows are emitted and the duplicate check deliberately exempts this source.
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

COLUMNS = ("value_date", "amount", "type", "narration", "utr")
CREDIT_MARKERS = frozenset({"CR", "CREDIT", "C"})
DEBIT_MARKERS = frozenset({"DR", "DEBIT", "D"})


def _direction_of(marker: str) -> Direction:
    """CR/DR to a canonical direction. A statement using neither is not a
    statement we can read, so it is refused rather than assumed."""
    token = marker.strip().upper()
    if token in CREDIT_MARKERS:
        return Direction.INFLOW
    if token in DEBIT_MARKERS:
        return Direction.OUTFLOW
    raise IngestError(f"unknown transaction type {marker!r} — expected CR or DR")


class BankAdapter:
    source = Source.BANK
    filename = "bank.csv"

    def load(self, path: Path) -> IngestResult:
        records: list[Record] = []
        failures: list[IngestFailure] = []

        try:
            require_columns(path, COLUMNS)
        except IngestError as exc:
            return IngestResult(failures=(IngestFailure(self.source, 1, str(exc)),))

        for line_no, row in read_rows(path):
            try:
                require(row, "utr", "amount", "value_date", "type")
                direction = _direction_of(row["type"])
                narration = row.get("narration", "").strip()
                records.append(
                    Record(
                        source=self.source,
                        external_id=row["utr"].strip(),
                        amount=abs(parse_amount(row["amount"])),
                        value_date=parse_business_date(row["value_date"]),
                        direction=direction,
                        narration=narration,
                        refs=extract_refs(narration, row["utr"]),
                        raw=dict(row),
                    )
                )
            except IngestError as exc:
                failures.append(IngestFailure(self.source, line_no, str(exc), dict(row)))

        return IngestResult(tuple(records), tuple(failures))
