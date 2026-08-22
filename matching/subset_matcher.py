"""L3 — N:1 Candidate Generation. Guide §4.3. Gate 7.

Knows payments, delegates arithmetic to subset_solver.

    target = fee_model.expected_gross(credit.amount)   # net -> gross
    window = business days back from credit.value_date  # T+2 + holiday buffer
    pool   = open ledger rows in window
             + open refunds in a WIDER window

Refunds are just negative numbers (§4.3a): they enter the same pool and the same
solver via Record.signed_amount. There must be NO `if is_refund:` branch — a
special branch means the abstraction is wrong.

The window asymmetry is the entire trick for CROSS_PERIOD_REFUND (§4.3b):
orders get the tight T+2 window, refunds get ~45 days.

Domain knowledge is a performance feature (§2.4): the window turns 2^30
combinations into 2^5.
"""
