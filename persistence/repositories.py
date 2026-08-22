"""Repository pattern. Guide §5.3, §5.6. Gate 8.

Core logic never sees SQL. Tests run against an in-memory repo.

Raw SQL through repositories, not an ORM — clearer for money, and faster to
debug at 3 a.m. (§5.3, explicitly rejected list).

Posting is wrapped in a transaction per entry with the idempotency key as the
guard, so partially completed runs never leave half-posted books (§9.5).
"""
