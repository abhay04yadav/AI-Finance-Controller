"""One module per exception type. Guide §6.2 step 3.

Every injector conforms to one protocol and follows one shape:
MUTATE THE WORLD -> RECORD THE TRUTH.

    class Injector(Protocol):
        reason_code: ReasonCode
        def inject(self, world, rng, count) -> list[TruthException]: ...

Eight injectors, planted as RATIOS of scale (never fixed counts) so behaviour is
comparable at 50 and at 5,000:

    AWAITING_SETTLEMENT   0.8%   defer WHOLE orders from a batch
    LATE_AUTHORIZATION    0.6%   mark failed, resurrect 2 days later
    AUTO_REFUNDED         0.4%   never captured within 3 days
    CROSS_PERIOD_REFUND   0.8%   deduct an old refund from the current batch
    HOLIDAY_SHIFT         0.6%   force T+2 onto a non-working day
    DUPLICATE_UTR         0.4%   duplicate one bank row
    MISSING_IN_LEDGER     0.4%   bank credit with no ledger entry
    ROUNDING_DRIFT        0.6%   +/- 1-50 paise fee rounding

Every injector must trace to a documented Razorpay or RBI behaviour (Appendix C).
If it cannot, it is an invented failure mode and it tests nothing real.

Partial settlement defers WHOLE TRANSACTIONS. It never splits an amount (§1.3).
Getting this wrong makes N:1 matching impossible.
"""
