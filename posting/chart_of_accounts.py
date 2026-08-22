"""Chart of accounts. Guide Appendix B. Gate 8.
Uses StrEnum, not `(str, Enum)` as written in guide §3.4. On Python 3.11+ the
two differ where it matters: with `(str, Enum)`, `str(Source.BANK)` is
"Source.BANK", not "bank". The guide's own §8.3 example builds a narration with
    f"Unreconciled credit {exc.ref} — {exc.reason_code}"
which would post "ReasonCode.AMOUNT_MISMATCH" into the books. StrEnum makes
str(x) == x.value, so enums are safe in f-strings, narrations, and JSONB.
requires-python is >=3.11, so StrEnum is always available.
"""

from enum import StrEnum


class Account(StrEnum):
    BANK = "1000 Bank Account"
    ACCOUNTS_RECEIVABLE = "1100 Accounts Receivable"
    SUSPENSE = "1900 Suspense"
    GST_INPUT_CREDIT = "1500 GST Input Credit"
    GATEWAY_FEE = "5100 Gateway Fee Expense"
    REFUNDS = "4100 Refunds & Chargebacks"
    ROUNDING_WRITEOFF = "5900 Rounding Write-off"
