"""Settlement CSV -> Record stream. Guide §4.0. Gate 4.

settlement.csv columns: settlement_id, utr, settle_date, gross, fee, gst, net, order_id

The settlement report is THE BRIDGE DOCUMENT (§1.2, §4.1): it is the only file
carrying both order_id and utr, which is what makes L1's two-hop join possible.
"""
