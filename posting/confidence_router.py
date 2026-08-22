"""Confidence routing. Guide §4.5. Gate 8.

    >= 0.95        auto-post          user sees nothing; it is in the books
    0.70 - 0.95    review queue       prepared entry + reason -> 2-second decision
    <  0.70        exception          WHAT / WHY / ACTION card

Thresholds are read from Settings (§5.8) and are DERIVED from the calibration
table (§7), never hardcoded here and never guessed. If the top bucket is not
100% precise, raise the threshold until it is (§2.5).
"""
