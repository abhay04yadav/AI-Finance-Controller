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


class JournalRepository(Protocol):
    """Somewhere balanced entries can be kept without the domain knowing how."""

    def post(self, entry: JournalEntry) -> bool: ...

    def all(self) -> tuple[JournalEntry, ...]: ...

    def __len__(self) -> int: ...


class InMemoryJournalRepository:
    """The default book of account.

    Insertion-ordered, so two runs produce the same ledger in the same order and
    the metrics fingerprint stays stable (§9.1).
    """

    def __init__(self) -> None:
        self._entries: dict[str, JournalEntry] = {}
        self._rejected_duplicates = 0

    def post(self, entry: JournalEntry) -> bool:
        """Persist an entry. Returns False if this exact posting already exists.

        Balance is re-checked here as well as in the builder: this is the last
        point before an entry becomes part of the books, and §9.4 makes that an
        invariant rather than a convention.
        """
        entry.assert_balanced()
        if entry.idempotency_key in self._entries:
            self._rejected_duplicates += 1
            return False
        self._entries[entry.idempotency_key] = entry
        return True

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
