"""Bank CSV -> Record stream. Guide §4.0. Gate 4.

bank.csv columns: value_date, amount, type, narration, utr

The bank statement is context-free (§1.4 reason 5). Narration is realistic
gateway noise like "NEFT RAZORPAYSETL88 CR" and contains NO order IDs, ever.
`refs` is populated by regex from whatever ID-shaped tokens the narration holds.

Bank rows may legitimately duplicate — that is the DUPLICATE_UTR signal (§4.0
step 5), not an ingest error.
"""
