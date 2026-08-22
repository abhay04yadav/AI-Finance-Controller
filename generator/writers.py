"""CSV + truth.json writers. Guide §6.2 steps 4-5. Gate 2.

    ledger.csv       order_id, amount, capture_date, status
    settlement.csv   settlement_id, utr, settle_date, gross, fee, gst, net, order_id
    bank.csv         value_date, amount, type, narration, utr

THE SINGLE MOST IMPORTANT LINE IN THE GENERATOR:
bank.csv narration must be realistic gateway noise like "NEFT RAZORPAYSETL88 CR"
and must NEVER contain order IDs. Leaking them makes the problem trivial and
every accuracy number reported meaningless.

Verified with your own eyes at gate 2:  grep -c "ORD-" data/seed42/bank.csv  ->  0

truth.json carries a generator_version; eval refuses to score a dataset from a
different major version (§6.3).
"""
