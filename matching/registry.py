"""Strategy registration. Registry + Factory. Guide §5.3.

Strategies self-register with a priority. Enabling or disabling a layer is
config, not code. Adding a chargeback matcher = 1 new class + 1 registry line,
zero existing logic modified (§5.9 scenario 1).
"""
