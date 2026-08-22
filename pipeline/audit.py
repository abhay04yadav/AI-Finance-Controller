"""Event bus -> audit trail. Observer pattern. Guide §5.3, §9.3.

Every accept, verdict, and action emits an event. Every posted entry must be
able to answer: WHO decided this, on WHAT evidence, WHEN, under WHICH prompt
version. In finance this is not optional.

Adding metrics collection means adding a subscriber, not editing the pipeline.
"""
