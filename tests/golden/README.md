# Golden datasets

Frozen small dataset + expected metrics. Guide §7.5, §9.7.

CI fails if precision drops below the frozen expectation. Populated at gate 3
once the generator (gate 2) and the eval harness exist.

Do not regenerate these casually. A golden dataset that moves whenever the code
moves is not a regression test.
