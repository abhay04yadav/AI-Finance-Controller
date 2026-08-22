"""Exception actions as Commands. Guide §8.3, §5.3. Gate 9.

ACTION is what closes the loop. Without it this is a report; with it, it is a
controller.

Each action is an object with is_available() / execute() / undo() / describe().
Reversible, and every execution writes an audit event (§9.3). The UI renders
whatever is_available() returns — NO hardcoded button lists in the frontend,
otherwise adding an action means editing React and the pattern earns nothing.

Every reason code must have at least one available action (gate 9 pass condition).
"""
