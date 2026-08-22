"""L1 — Deterministic Match. Guide §4.1. Gate 5. ** THE CRITICAL GATE **

The two-hop join, because no single key spans all three files:
    ledger.order_id -> settlement.order_id
                       settlement.utr -> bank.utr

L1 declares confidence 1.00, so L1 precision must be EXACTLY 100.0% on every
seed. Not 99.7%. If a confidence-1.00 match can be wrong, the whole calibration
story (§2.5) is false and every downstream layer inherits the error.

A UTR appearing twice in the bank file is NOT matched — it flags DUPLICATE_UTR
immediately (§4.1 step 5). Matching one of them double-posts revenue.
"""
