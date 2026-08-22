"""Deterministic exception classification. Guide §8.2, Appendix A. Gate 9.

Every exception card carries exactly three things:
  WHAT   deterministic — generated from the reason code + the facts
  WHY    the hypothesis — from L4 Job B, or from this classifier when the
         reason code is unambiguous. NEVER a template string like "Match not
         found" reused across records; the WHY must be specific to that record.
  ACTION Command objects (§8.3)

AWAITING_SETTLEMENT is NOT an error (Appendix A). The money is genuinely in
transit. It needs its own visual treatment, separate from true exceptions.
"""
