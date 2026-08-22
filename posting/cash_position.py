"""Cash position — the answer to the track's title. Guide §1.6, §4.5. Gate 8.

    confirmed_in_bank   posted to BANK
    in_transit          AWAITING_SETTLEMENT — real money, not yet arrived
    in_suspense         arrived, unexplained
    revenue_recognised / fee_expense / gst_claimable

Unmatched bank credits go to SUSPENSE so the bank balance in the books ties to
the actual bank balance. The suspense balance IS the size of the unreconciled
problem — a controller reads that one number as "how much do I still not
understand?"

Tie-out invariant (§9.4): bank_account + suspense == sum(bank statement credits).
"""
