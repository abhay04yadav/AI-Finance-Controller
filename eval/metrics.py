"""Metrics container + report rendering. Guide §7.2, §7.4. Gate 3.

Seven metrics on the sheet:
    match rate          attempted / total
    match precision     correct / attempted        <- the real accuracy number
    exception recall    planted caught / planted total
    auto-resolution     conf >= 0.95 / total       <- the business value
    throughput          records/sec
    LLM calls and cost  calls, cost per 100 records
    calibration         precision per confidence bucket

Plus the line worth more than a clean 100%: the self-reported MISS, named, in
the output. Numbers are never rounded up (§2.8).

Every run dumps to eval/runs/{ts}.json for regression tracking (§7.5).
"""
