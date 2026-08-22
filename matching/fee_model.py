"""L2 — Fee Model Inference. Guide §4.2, §2.3. Gate 6.

The MDR is merchant-specific and usually unknown, so we DERIVE it rather than
configure it:

    net   = gross * (1 - 1.18r)
    r     = (1 - net/gross) / 1.18      <- infer from L1's confident pairs
    gross = net / (1 - 1.18r)           <- invert for unknown credits

MEDIAN, not mean — one international-card row at 3.5% drags a mean and quietly
breaks every downstream match. Requires >= 5 samples; below that the model is
marked LOW_CONFIDENCE and L3 widens its amount tolerance.

There is no configured rate in the normal path (§2.3). That is the whole point:
the system runs on any merchant's export with zero setup.
"""
