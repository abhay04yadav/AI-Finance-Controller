"""Double-entry journal construction. Builder pattern. Guide §4.5, §5.3. Gate 8.

A matched Rs 8,000 order does not post as "Rs 8,000 received". It decomposes:

    Bank Account            Dr.  Rs 7,811.20    <- what actually arrived
    Gateway Fee (expense)   Dr.  Rs   160.00    <- MDR
    GST Input Credit        Dr.  Rs    28.80    <- reclaimable tax
            To Accounts Receivable  Rs 8,000.00 <- what the customer owed

GST is a SEPARATE LINE. Collapse it into the fee line and the merchant silently
forfeits reclaimable tax — real money over a year, and a concrete business win
for the demo.

assert_balanced() runs on EVERY entry before it can be persisted (§9.4). A
reconciliation tool that produces unbalanced books is worse than none.

Idempotency key = sha256(sorted(ledger_ids) + utr + settlement_id), with a
unique index in Postgres. Reposting is a no-op, not a duplicate.
"""
