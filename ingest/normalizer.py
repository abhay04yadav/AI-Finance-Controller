"""Shared normalization: amounts, business dates, reference tokens.
Guide §4.0, §2.7 rules 1 and 6.

Every value that crosses the ingest boundary passes through exactly one of these
functions, so there is one definition of "what a date is" and one of "what an
amount is" for the whole system.

Two rules this module exists to enforce, both from §4.0:

  * **Reject rather than guess.** `03/04/2026` could be 3 April or 4 March. A
    reconciliation system that picks one silently shifts a settlement window and
    produces a plausible, wrong answer.
  * **Never repair.** A malformed row becomes an `IngestFailure` that surfaces on
    the exception page as `INGEST_ERROR`. It is not skipped and it is not fixed
    up. Silent repair is how reconciliation systems lose money.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

from core.dates import IST
from core.models import Record, Source
from core.money import Money, MoneyParseError
from core.reason_codes import ReasonCode

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class IngestError(ValueError):
    """A row could not be read unambiguously. Never raised past the adapter —
    it is captured as an `IngestFailure` so one bad row cannot abort a run."""


@dataclass(frozen=True, slots=True)
class IngestFailure:
    """One row that could not be normalized, preserved for the exception page.

    Carries the original row verbatim: a controller needs to see what actually
    arrived, not a cleaned-up version of it.
    """

    source: Source
    line_no: int
    reason: str
    raw: dict[str, str] = field(default_factory=dict)
    reason_code: ReasonCode = ReasonCode.INGEST_ERROR

    def describe(self) -> str:
        return f"{self.source}:{self.line_no} — {self.reason}"


@dataclass(frozen=True, slots=True)
class IngestResult:
    """The whole L0 output: what was read, and what could not be."""

    records: tuple[Record, ...] = ()
    failures: tuple[IngestFailure, ...] = ()

    def by_source(self, source: Source) -> tuple[Record, ...]:
        return tuple(r for r in self.records if r.source is source)

    def total_paise(self, source: Source) -> int:
        """Signed total for one source — the figure that must tie to the file."""
        return sum(r.signed_amount for r in self.by_source(source))


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

#: All-numeric day/month/year forms. `03/04/2026` is 3 April in India and
#: 4 March in the US, and nothing in the file says which. Rejected as a class
#: rather than case by case: accepting `13/04/2026` because 13 cannot be a month
#: while rejecting `03/04/2026` would make the parser's behaviour depend on the
#: data, which is worse than refusing both.
_AMBIGUOUS_NUMERIC = re.compile(r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$")

#: Self-describing formats: ISO, or anything naming the month.
_ACCEPTED_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%b-%Y",
)


def parse_business_date(text: str) -> date:
    """Parse a business date in Asia/Kolkata. Guide §2.7 rule 6, §9.2.

    Accepts ISO dates and month-named forms. Accepts an ISO datetime *only* when
    it carries an offset, converting it to the IST calendar date — a UTC
    timestamp after 18:30 IST belongs to the next Indian business day, and
    treating it otherwise shifts settlement windows by a day.

    Rejects ambiguous all-numeric forms and naive datetimes.
    """
    if not isinstance(text, str):
        raise IngestError(f"expected a date string, got {type(text).__name__}")

    raw = text.strip()
    if not raw:
        raise IngestError("empty date")

    if _AMBIGUOUS_NUMERIC.match(raw):
        raise IngestError(
            f"ambiguous date {raw!r} — day/month order is unknowable from the "
            "file; refusing to guess"
        )

    # ISO datetime: only acceptable with an explicit offset.
    if "T" in raw or (" " in raw and ":" in raw):
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise IngestError(f"unparseable timestamp {raw!r}") from exc
        if stamp.tzinfo is None:
            raise IngestError(
                f"naive datetime {raw!r} — no timezone, so the business date is "
                "undefined (§2.7 rule 6)"
            )
        return stamp.astimezone(IST).date()

    for fmt in _ACCEPTED_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    raise IngestError(
        f"unrecognised date format {raw!r} — accepted forms are ISO "
        "(2026-08-04) or a named month (04-Aug-2026)"
    )


# --------------------------------------------------------------------------
# Amounts
# --------------------------------------------------------------------------


def parse_amount(text: str) -> Money:
    """Parse straight to integer paise. Never via float (§2.7 rule 1)."""
    try:
        return Money.from_rupee_string(text)
    except (MoneyParseError, TypeError) as exc:
        raise IngestError(f"unparseable amount {text!r}: {exc}") from exc


# --------------------------------------------------------------------------
# Reference tokens
# --------------------------------------------------------------------------

#: The generic shape from §4.0: an uppercase prefix and at least four digits.
_GENERIC_REF = re.compile(r"\b([A-Z]{2,6}[-_]?\d{4,})\b")

#: "...plus known prefixes" (§4.0). The generic pattern alone finds nothing in a
#: narration like "NEFT-RAZORPAY-SETL101-CR", because SETL101 carries only three
#: digits — and the settlement number is exactly the signal L4 needs to pick
#: between candidates (§4.4 job A).
#:
#: Each prefix is scanned separately rather than as one alternation: against
#: "RZPSETL104" a combined pattern matches the longest branch and yields
#: "RZPSETL104", which joins to nothing. Scanning "SETL" on its own also finds
#: "SETL104" inside it, which is the token that reaches settlement SETL-104.
_KNOWN_PREFIXES: Final = ("SETL", "UTR", "ORD", "RFND")

_SEPARATORS: Final = re.compile(r"[-_]")


def extract_refs(*texts: str) -> frozenset[str]:
    """Every identifier-shaped token found anywhere in the row. Guide §3.4.

    Deliberately a set, not a typed field: real bank narrations bury references
    in noise, and a set lets the matcher attempt joins without knowing in
    advance which column carried the key.
    """
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        upper = text.upper()
        found.update(_GENERIC_REF.findall(upper))
        for prefix in _KNOWN_PREFIXES:
            found.update(re.findall(rf"({prefix}[-_]?\d+)", upper))

    # Both the literal token and a separator-free form, so a narration reading
    # "SETL101" and a settlement_id reading "SETL-101" meet on common ground.
    # This is why refs is a set and not a typed field (§3.4).
    return frozenset(found | {_SEPARATORS.sub("", token) for token in found})


# --------------------------------------------------------------------------
# CSV reading
# --------------------------------------------------------------------------


def read_rows(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield (line number, row). Line numbers are 1-based including the header,
    so they match what a controller sees opening the file."""
    with path.open(encoding="utf-8", newline="") as fh:
        yield from enumerate(csv.DictReader(fh), start=2)


def require(row: dict[str, str], *columns: str) -> None:
    """Fail loudly on a missing or empty required column."""
    for column in columns:
        if row.get(column) in (None, ""):
            raise IngestError(f"missing required column {column!r}")


def require_columns(path: Path, expected: tuple[str, ...]) -> None:
    """Check the header before reading a single row.

    A file whose columns do not match is a different file, not a broken row —
    reporting that as N row-level failures would bury the actual problem.
    """
    with path.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh), [])
    missing = [c for c in expected if c not in header]
    if missing:
        raise IngestError(f"{path.name} is missing column(s) {missing}; found {header}")


# --------------------------------------------------------------------------
# Duplicate validation (§4.0 step 5)
# --------------------------------------------------------------------------


def find_illegal_duplicates(records: tuple[Record, ...]) -> list[IngestFailure]:
    """No duplicate (source, external_id) — except in the bank file.

    A repeated UTR in a bank statement is a real signal (DUPLICATE_UTR), not an
    ingest fault, and swallowing it here would let L1 double-post revenue. A
    repeated order or settlement id is a genuinely corrupt export.
    """
    seen: dict[tuple[Source, str], int] = {}
    failures: list[IngestFailure] = []
    for record in records:
        if record.source is Source.BANK:
            continue
        key = (record.source, record.external_id)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            failures.append(
                IngestFailure(
                    source=record.source,
                    line_no=-1,
                    reason=(
                        f"duplicate {record.source} id {record.external_id!r} — "
                        "only bank rows may legitimately repeat"
                    ),
                    raw={"external_id": record.external_id},
                )
            )
    return failures


# --------------------------------------------------------------------------
# The L0 entry point
# --------------------------------------------------------------------------


def load_dataset(dataset: Path, adapters: Any = None) -> IngestResult:
    """Read all three sources into one canonical Record stream.

    Records are sorted by (source, value_date, external_id) so that two runs over
    the same files produce an identical list — set iteration order would
    otherwise make the pipeline non-deterministic (§9.1).
    """
    from ingest.bank_adapter import BankAdapter
    from ingest.ledger_adapter import LedgerAdapter
    from ingest.settlement_adapter import SettlementAdapter

    if adapters is None:
        adapters = (LedgerAdapter(), SettlementAdapter(), BankAdapter())

    records: list[Record] = []
    failures: list[IngestFailure] = []

    for adapter in adapters:
        path = dataset / adapter.filename
        if not path.exists():
            failures.append(
                IngestFailure(
                    source=adapter.source,
                    line_no=0,
                    reason=f"{adapter.filename} not found in {dataset}",
                )
            )
            continue
        result = adapter.load(path)
        records.extend(result.records)
        failures.extend(result.failures)

    ordered = tuple(
        sorted(records, key=lambda r: (str(r.source), r.value_date, r.external_id))
    )
    failures.extend(find_illegal_duplicates(ordered))
    return IngestResult(records=ordered, failures=tuple(failures))
