"""Chart of accounts. Guide Appendix B. Gate 8."""

from enum import Enum


class Account(str, Enum):
    BANK = "1000 Bank Account"
    ACCOUNTS_RECEIVABLE = "1100 Accounts Receivable"
    SUSPENSE = "1900 Suspense"
    GST_INPUT_CREDIT = "1500 GST Input Credit"
    GATEWAY_FEE = "5100 Gateway Fee Expense"
    REFUNDS = "4100 Refunds & Chargebacks"
    ROUNDING_WRITEOFF = "5900 Rounding Write-off"
