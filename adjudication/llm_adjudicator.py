"""LLM adjudication. Guide §4.4. Gate 11 of 14 — deliberately last.

Two jobs, one layer:

  **Job A — many candidates: SELECT.** Several combinations of ledger rows sum
  to the same figure, so the amount cannot separate them. The narration, the
  settlement batch and the capture timing can. Those three signals are the only
  thing this layer is asked about.

  **Job B — zero candidates: CLASSIFY and hypothesise.** Nothing adds up. The
  answer becomes the WHY and the ACTION on the exception card (§8.2). This is
  where the model earns its place: code can rank near-misses, only a model can
  say why ₹80 is missing in language a controller can act on.

Four rules this file exists to enforce, all of them checkable:

1. **Batched, never one call per row.** The interface takes a sequence; a loop
   over single calls is not expressible. One request per job, per run.
2. **Budgeted.** At most `budget` cases reach L4, taken in a deterministic
   order. Everything past the cap stays an exception and is named as skipped.
   The budget is enforced here, not merely measured in the report.
3. **Cached.** Content-addressed on the question, the prompt version and the
   model. A cached run makes no request at all and produces identical bytes.
4. **Verified.** Every answer goes through `guardrails`. A rejected verdict does
   NOT retry — it falls through to ADJUDICATION_REJECTED.

**On §4.4's "temperature 0".** Claude Opus 5 does not accept a `temperature`
parameter; sending one returns a 400. The determinism §9.1 requires therefore
comes from the cache rather than from a sampling knob — see `cache.py` for why
that is the stronger of the two guarantees rather than a substitute for it.

**On server-side refusal fallbacks.** Deliberately not enabled. A fallback
silently re-runs the request on a different model, and this system stamps the
model id onto every verdict and into every cache key precisely so that an
auditor can tell which model decided what. Swapping models mid-run to rescue an
answer trades the property this layer is built around for an answer we do not
need: a refusal here is handled as a declined verdict, and a declined verdict is
already an honest line on the exception list.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from adjudication import guardrails, prompts
from adjudication.cache import CacheCorrupt, VerdictCache
from adjudication.schemas import (
    BatchHypothesisResponse,
    BatchVerdictResponse,
    HypothesisResponse,
    VerdictResponse,
    json_schema,
)
from core.adjudication import (
    AdjudicationResult,
    Ambiguity,
    Hypothesis,
    Unexplained,
    Verdict,
)
from core.result import Ok

#: The model every verdict is stamped with. Part of the cache key, so changing
#: it invalidates every cached answer rather than mixing two models' judgements
#: in one report.
MODEL = "claude-opus-5"

#: Room for a whole batch of verdicts plus adaptive thinking. Not lowballed:
#: a truncated response costs a retry, and a retry costs determinism.
MAX_TOKENS = 16000

#: Selecting between two pre-verified combinations on named evidence is a small
#: reasoning task. `high` would buy nothing here and spend tokens on every run.
EFFORT = "low"

#: List price at the time of writing, $5.00 / $25.00 per MTok (Claude Opus 5),
#: converted at ₹88 to the dollar. Stated as constants rather than hidden in a
#: formula so the cost line in the report can be checked rather than believed.
USD_PER_MTOK_IN = 5.00
USD_PER_MTOK_OUT = 25.00
PAISE_PER_USD = 8800


class LlmAdjudicator:
    """L4, with the LLM on a short leash."""

    name = "L4_llm"

    def __init__(
        self,
        *,
        cache: VerdictCache | None = None,
        client: Any | None = None,
        allow_network: bool = True,
    ) -> None:
        self._cache = cache or VerdictCache()
        self._client = client
        self._allow_network = allow_network
        self._client_error: str | None = None

    # ------------------------------------------------------------------ api

    def adjudicate(
        self,
        ambiguities: Sequence[Ambiguity],
        cases: Sequence[Unexplained],
        *,
        budget: int,
    ) -> AdjudicationResult:
        notes: list[str] = []

        # Deterministic order first, THEN the cap. Sorting after capping would
        # make the same run drop different cases depending on dict ordering.
        job_a = sorted(ambiguities, key=lambda a: a.credit_utr)
        job_b = sorted(cases, key=lambda c: c.ref)

        # Job A first: it can turn an exception into a match, which is worth
        # more than improving the prose on one that will stay an exception.
        allowance = max(budget, 0)
        taken_a = job_a[:allowance]
        allowance -= len(taken_a)
        taken_b = job_b[:allowance]

        skipped = tuple(
            [a.credit_utr for a in job_a[len(taken_a):]]
            + [c.ref for c in job_b[len(taken_b):]]
        )
        if skipped:
            notes.append(
                f"LLM budget of {budget} case(s) reached; {len(skipped)} left "
                "unadjudicated on the exception list."
            )

        verdicts, rejections, abstentions, usage_a, notes_a = self._run_job_a(taken_a)
        hypotheses, hyp_rejections, usage_b, notes_b = self._run_job_b(taken_b)
        notes += notes_a + notes_b

        return AdjudicationResult(
            verdicts=verdicts,
            rejections=rejections,
            abstentions=abstentions,
            hypotheses=hypotheses,
            hypothesis_rejections=hyp_rejections,
            # Cases L4 actually ANSWERED, from cache or from the API. Not
            # cases submitted: a run with no credential asked nothing and must
            # not report LLM calls it never made.
            calls=usage_a.answered + usage_b.answered,
            api_requests=usage_a.requests + usage_b.requests,
            cost_paise=usage_a.cost_paise + usage_b.cost_paise,
            skipped_over_budget=skipped,
            notes=tuple(notes),
        )

    # --------------------------------------------------------------- job A

    def _run_job_a(
        self, ambiguities: Sequence[Ambiguity]
    ) -> tuple[dict[str, Verdict], dict[str, str], dict[str, str], _Usage, list[str]]:
        verdicts: dict[str, Verdict] = {}
        rejections: dict[str, str] = {}
        abstentions: dict[str, str] = {}
        notes: list[str] = []
        if not ambiguities:
            return verdicts, rejections, abstentions, _Usage(), notes

        version = prompts.version(prompts.JOB_A)
        raw, pending, usage, notes = self._answers(
            items={a.credit_utr: a for a in ambiguities},
            key_of=lambda a: _key(a.cache_key(), version),
            prompt_name=prompts.JOB_A,
            payload_key="credits",
            response_model=BatchVerdictResponse,
            collection="verdicts",
            id_field="utr",
            version=version,
        )
        for utr, payload in raw.items():
            ambiguity = pending[utr]
            parsed = VerdictResponse.model_validate(_body(payload))
            verdict = parsed.to_verdict(
                prompt_version=version,
                model=MODEL,
                origin=payload.get("_origin", "api"),
            )
            checked = guardrails.verify(verdict, ambiguity)
            if not isinstance(checked, Ok):
                rejections[utr] = checked.unwrap_err()
            elif checked.value.is_abstention:
                # It looked and declined. No match, no rejection — an answer.
                abstentions[utr] = checked.value.reason
            else:
                # The model's own confidence is never the last word. A verdict
                # that reached the auto-post band would let an LLM move money
                # without a human; capping it one notch below routes every
                # adjudicated match to the review queue with its prepared entry
                # and its reason attached (§2.5, §4.5).
                verdicts[utr] = _capped(checked.value)
        return verdicts, rejections, abstentions, usage, notes

    # --------------------------------------------------------------- job B

    def _run_job_b(
        self, cases: Sequence[Unexplained]
    ) -> tuple[dict[str, Hypothesis], dict[str, str], _Usage, list[str]]:
        hypotheses: dict[str, Hypothesis] = {}
        rejections: dict[str, str] = {}
        notes: list[str] = []
        if not cases:
            return hypotheses, rejections, _Usage(), notes

        version = prompts.version(prompts.JOB_B)
        raw, pending, usage, notes = self._answers(
            items={c.ref: c for c in cases},
            key_of=lambda c: _key(c.cache_key(), version),
            prompt_name=prompts.JOB_B,
            payload_key="credits",
            response_model=BatchHypothesisResponse,
            collection="hypotheses",
            id_field="ref",
            version=version,
        )
        for ref, payload in raw.items():
            case = pending[ref]
            parsed = HypothesisResponse.model_validate(_body(payload))
            hypothesis = parsed.to_hypothesis(
                prompt_version=version,
                model=MODEL,
                origin=payload.get("_origin", "api"),
            )
            checked = guardrails.verify_hypothesis(hypothesis, case)
            if isinstance(checked, Ok):
                hypotheses[ref] = checked.value
            else:
                rejections[ref] = checked.unwrap_err()
        return hypotheses, rejections, usage, notes

    # ------------------------------------------------------- cache + call

    def _answers(
        self,
        *,
        items: dict[str, Any],
        key_of: Any,
        prompt_name: str,
        payload_key: str,
        response_model: type[Any],
        collection: str,
        id_field: str,
        version: str,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any], _Usage, list[str]]:
        """Serve from cache, ask the API for whatever is left, cache the rest.

        Returns the raw answer dicts keyed by ref, so the two jobs share every
        line of the cache, budget and transport logic and differ only in their
        schema.
        """
        answers: dict[str, dict[str, Any]] = {}
        misses: dict[str, Any] = {}
        notes: list[str] = []

        for ref, item in items.items():
            try:
                cached = self._cache.get(key_of(item))
            except CacheCorrupt as exc:
                # Caught by type and reported, not swallowed (§5.5). A corrupt
                # entry is a real event; it is not a reason to stop reconciling.
                notes.append(f"cache entry unreadable, re-asking: {exc}")
                cached = None
            if cached is not None:
                answers[ref] = {**cached, "_origin": "cache"}
            else:
                misses[ref] = item

        if not misses:
            return answers, items, _Usage(len(answers)), notes

        client = self._get_client()
        if client is None:
            notes.append(
                f"{len(misses)} case(s) had no cached verdict and no API "
                f"credential was available ({self._client_error}); they stay on "
                "the exception list rather than being guessed."
            )
            return answers, items, _Usage(len(answers)), notes

        payload = {payload_key: [item.as_prompt_dict() for item in misses.values()]}
        try:
            fresh, usage = self._ask(
                client=client,
                system=prompts.load(prompt_name),
                payload=payload,
                response_model=response_model,
            )
        except LlmUnavailable as exc:
            notes.append(f"L4 unavailable, {len(misses)} case(s) left unadjudicated: {exc}")
            return answers, items, _Usage(len(answers)), notes

        returned = {
            str(entry[id_field]): entry
            for entry in fresh.get(collection, [])
            if isinstance(entry, dict) and entry.get(id_field)
        }
        for ref, item in misses.items():
            entry = returned.get(ref)
            if entry is None:
                # Asked and not answered. Not an error worth raising, but not
                # something to paper over either: the case stays an exception.
                notes.append(f"{ref}: no answer returned for a case that was asked")
                continue
            body = {k: v for k, v in entry.items() if k != id_field}
            self._cache.put(
                key_of(item),
                body,
                meta={"model": MODEL, "prompt_version": version, "ref": ref},
            )
            answers[ref] = {**body, "_origin": "api"}

        return answers, items, replace(usage, answered=len(answers)), notes

    def _ask(
        self,
        *,
        client: Any,
        system: str,
        payload: dict[str, Any],
        response_model: type[Any],
    ) -> tuple[dict[str, Any], _Usage]:
        """One request. One.

        The system prompt is stable across every run and carries a cache
        breakpoint; the question — the only volatile part — goes last, in the
        user turn, so the prefix stays cacheable.
        """
        import anthropic

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(payload, sort_keys=True, default=str),
                    }
                ],
                output_config={
                    "effort": EFFORT,
                    "format": {
                        "type": "json_schema",
                        "schema": json_schema(response_model),
                    },
                },
            )
        except anthropic.APIStatusError as exc:
            raise LlmUnavailable(f"{type(exc).__name__}: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise LlmUnavailable(f"network: {exc}") from exc
        except TypeError as exc:
            # The SDK's own signal for "no credential could be resolved", raised
            # from header validation rather than as an AnthropicError. Converted
            # to an expected outcome and reported verbatim, never hidden (§5.5).
            raise LlmUnavailable(f"authentication: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LlmUnavailable("the model declined to answer")
        if response.stop_reason == "max_tokens":
            # Truncated JSON is not partially usable, and retrying at a larger
            # budget would make the run non-reproducible against the cache.
            raise LlmUnavailable("response hit max_tokens and is incomplete")

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            parsed = response_model.model_validate_json(text)
        except ValueError as exc:
            raise LlmUnavailable(f"response did not match the contract: {exc}") from exc

        usage = response.usage
        cost = _cost_paise(usage.input_tokens, usage.output_tokens)
        return parsed.model_dump(), _Usage(requests=1, cost_paise=cost)

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if not self._allow_network:
            self._client_error = "network disabled"
            return None
        try:
            import anthropic
        except ImportError:
            self._client_error = 'the anthropic package is not installed (pip install -e ".[llm]")'
            return None
        try:
            # Zero-arg: resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or a
            # stored `ant auth login` profile. An unset env var does not mean
            # there are no credentials, which is why nothing here reads os.environ.
            client = anthropic.Anthropic()
        except anthropic.AnthropicError as exc:
            self._client_error = str(exc)
            return None

        # Construction succeeds with no credentials at all — the SDK only
        # notices at request time, and it raises TypeError when it does. Found
        # the hard way: without this check the first uncached run on a machine
        # with no key died with a stack trace in the middle of a reconciliation
        # instead of reporting three unadjudicated exceptions. A demo is exactly
        # where that happens.
        if (
            client.api_key is None
            and client.auth_token is None
            and getattr(client, "credentials", None) is None
        ):
            self._client_error = (
                "no API credential resolved (set ANTHROPIC_API_KEY, or run "
                "`ant auth login`)"
            )
            return None

        self._client = client
        return self._client


@dataclass(frozen=True, slots=True)
class _Usage:
    """What one job cost, in the three units that matter separately."""

    answered: int = 0
    requests: int = 0
    cost_paise: int = 0


class LlmUnavailable(RuntimeError):
    """The model could not be asked, or answered something unusable.

    An expected outcome, not a bug: it is reported on the run and the affected
    cases stay on the exception list. Never retried — see the module docstring.
    """


#: One notch below `Settings.auto_post_threshold`. An LLM adjudication produces
#: a prepared entry for a human to approve, never a posting.
MAX_ADJUDICATED_CONFIDENCE = 0.94


def _capped(verdict: Verdict) -> Verdict:
    if verdict.confidence <= MAX_ADJUDICATED_CONFIDENCE:
        return verdict
    return replace(verdict, confidence=MAX_ADJUDICATED_CONFIDENCE)


def _body(payload: dict[str, Any]) -> dict[str, Any]:
    """The answer without our own bookkeeping.

    `_origin` records whether this came from the cache or the wire; the schemas
    are `extra="forbid"` so that a response carrying a field we never asked for
    is a parse failure, and our own annotation must not be the thing that trips
    it.
    """
    return {k: v for k, v in payload.items() if not k.startswith("_")}


def _key(question: str, version: str) -> str:
    """The cache key: the question, the prompt version and the model."""
    import hashlib

    stamp = hashlib.sha256(f"{version}|{MODEL}".encode()).hexdigest()[:8]
    return f"{question}-{stamp}"


def _cost_paise(input_tokens: int, output_tokens: int) -> int:
    usd = (
        input_tokens * USD_PER_MTOK_IN + output_tokens * USD_PER_MTOK_OUT
    ) / 1_000_000
    return round(usd * PAISE_PER_USD)
