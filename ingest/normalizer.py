"""Shared normalization: amounts -> Money(paise), dates -> business dates, ref tokenizer.
Guide §4.0. Gate 4.

Two traps this module must not fall into:
  - Parse amounts STRAIGHT to paise. Never parse to float then int (gate 4 red flag).
  - Do not "helpfully" repair bad rows. A malformed row is an INGEST_ERROR that
    surfaces on the exception page. Silent repair is how reconciliation systems
    lose money. Ambiguous dates like "03/04/2026" raise rather than guess.
"""
