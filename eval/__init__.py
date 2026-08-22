"""The eval harness. truth.json + agent output = marksheet. Guide §7. GATE 3.

Built BEFORE the agent works. It should score a stub agent at 0%. That is correct.

It must read ONLY truth.json and the agent public output, never the agent
internals. Otherwise you are grading the exam with the answer key visible to the
student (gate 3 red flag).
"""
