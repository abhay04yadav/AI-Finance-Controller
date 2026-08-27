"""Repository pattern. Guide §5.3, §5.6, §9.5.

Core logic never sees SQL. Tests — and the demo — run against the in-memory
repository, so a clean clone reconciles 5,000 records with no database to
install (§9.9's 30-second quickstart, and gate 14's five-minute clean clone).
The Postgres schema in `schema.sql` is the same shape and the same unique index.

**Idempotency lives here, not in the caller.** §4.5 requires that reposting is a
no-op rather than a duplicate, and the guarantee is only worth anything if it
cannot be forgotten at a call site: `post()` refuses a key it has already seen,
exactly as the `UNIQUE` index on `journal_entries.idempotency_key` would.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from core.models import JournalEntry

#: Journal entries are numbered from here. A book of account starts at 1; the
#: number is the book's, not the entry's, which is why it is issued on `post()`
#: and not carried on `JournalEntry`. The same entry posted into two different
#: books gets two different numbers, and that is correct.
FIRST_ENTRY_NUMBER = 1


def format_entry_number(sequence: int) -> str:
    """`JE-0001`. Zero-padded so the column stays decimal-aligned under
    `tabular-nums` — a ledger where JE-9 and JE-1000 sit ragged reads as a
    spreadsheet, not a book (§8.5)."""
    return f"JE-{sequence:04d}"


class JournalRepository(Protocol):
    """Somewhere balanced entries can be kept without the domain knowing how."""

    def post(self, entry: JournalEntry) -> bool: ...

    def all(self) -> tuple[JournalEntry, ...]: ...

    def number_for(self, idempotency_key: str) -> str | None: ...

    def __len__(self) -> int: ...


class InMemoryJournalRepository:
    """The default book of account.

    Insertion-ordered, so two runs produce the same ledger in the same order and
    the metrics fingerprint stays stable (§9.1).
    """

    def __init__(self) -> None:
        self._entries: dict[str, JournalEntry] = {}
        self._numbers: dict[str, str] = {}
        self._rejected_duplicates = 0

    def post(self, entry: JournalEntry) -> bool:
        """Persist an entry. Returns False if this exact posting already exists.

        Balance is re-checked here as well as in the builder: this is the last
        point before an entry becomes part of the books, and §9.4 makes that an
        invariant rather than a convention.

        A successful post is issued the next journal number. Refused duplicates
        get none — the point of refusing is that nothing was written, and a
        number handed out for a posting that did not happen would leave a gap
        in the sequence that nobody could explain.
        """
        entry.assert_balanced()
        if entry.idempotency_key in self._entries:
            self._rejected_duplicates += 1
            return False
        self._entries[entry.idempotency_key] = entry
        self._numbers[entry.idempotency_key] = format_entry_number(
            FIRST_ENTRY_NUMBER + len(self._numbers)
        )
        return True

    def number_for(self, idempotency_key: str) -> str | None:
        """The journal number this book issued, or None if it holds no such entry."""
        return self._numbers.get(idempotency_key)

    def entry_for(self, idempotency_key: str) -> JournalEntry | None:
        return self._entries.get(idempotency_key)

    def numbered(self) -> tuple[tuple[str, JournalEntry], ...]:
        """Every entry with the number it was issued, in posting order."""
        return tuple(
            (self._numbers[key], entry) for key, entry in self._entries.items()
        )

    def all(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[JournalEntry]:
        return iter(self._entries.values())

    @property
    def rejected_duplicates(self) -> int:
        """How many repostings were refused. Zero on a first run, by definition."""
        return self._rejected_duplicates

    def balance(self, account: str) -> int:
        """Signed balance of one account: debits positive, credits negative."""
        return sum(e.amount_for(account) for e in self._entries.values())

    def assert_books_balance(self) -> None:
        """Run-level invariant: total debits equal total credits (§9.4)."""
        debits = sum(e.total_debits for e in self._entries.values())
        credits = sum(e.total_credits for e in self._entries.values())
        if debits != credits:
            raise ValueError(f"books do not balance: {debits} != {credits}")
