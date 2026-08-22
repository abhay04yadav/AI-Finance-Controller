"""ReconciliationPipeline — Chain of Responsibility. Guide §5.4.

Owns WIRING, not business logic. Each layer consumes the RESIDUAL of the
previous one; nothing L1 resolved is ever reconsidered by L3. That is what keeps
the LLM budget under 10%.

This file must contain NO fee arithmetic, NO date math, NO SQL, and NO prompt
text. If business logic appears here during the build, it belongs somewhere
else. (Review Guide part 1: "business logic in orchestrator.py" = the design has
collapsed.)

Depends on the MatchStrategy / Adjudicator / Poster protocols, never on the
concretes. Concretes are wired in api/deps.py (§5.4, DIP).
"""
