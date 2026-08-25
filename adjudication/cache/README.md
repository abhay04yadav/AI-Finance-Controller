# Verdict cache

One JSON file per adjudicated case, named by a SHA-256 of the serialized
question plus the prompt version plus the model id.

**This directory is meant to be committed.** It is what makes the LLM path
reproducible: a reviewer with no API credential can clone the repo, run
`make eval`, and get byte-identical verdicts to the ones in our report. §4.4
asks for temperature 0 to achieve that; Claude Opus 5 does not accept a
temperature parameter, and a content-addressed cache is the stronger guarantee
anyway — see `../cache.py`.

**It is empty right now, and that is deliberate.** No verdict has been recorded
because no model has been asked: this build has never had an API credential
available. Seeding it with hand-written answers would be fabricating LLM output
and reporting it as measured. `make eval` therefore reports `LLM calls 0`, which
is the truth.

To populate it, set `ANTHROPIC_API_KEY` (or run `ant auth login`) and run
`make eval`. Every case that reaches L4 will be asked once, and never again.

Delete a file to force one case to be re-asked. Delete the directory to re-ask
everything.
