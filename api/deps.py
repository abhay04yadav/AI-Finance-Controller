"""DI container — the only place concretes are named. Guide §5.4 (DIP), §5.9.

Swapping the LLM provider is one line here; the orchestrator, matchers, and
tests stay untouched.

This is also where the SINGLE BusinessCalendar instance is constructed and
handed to both the generator and the matcher (§5.1). Two different calendar
instances make planted HOLIDAY_SHIFT cases unsolvable by construction, and that
bug looks exactly like a matcher failure.
"""
