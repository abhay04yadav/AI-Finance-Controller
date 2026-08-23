"""Result[T, E] — explicit outcomes, no bare exceptions. Guide §5.5.

The rule that keeps money from disappearing:

  * **Expected outcomes are values.** "No candidates found" is `Ok([])`.
    "Verdict failed guardrails" is `Err("arithmetic_mismatch")`. These flow to
    the exception list, where a human can see them.
  * **Exceptions are for bugs.** Unbalanced journal entry, float money, unknown
    source — raise, fail loudly, fix the code.
  * **Never swallow.** A bare `except: pass` in a reconciliation system is how
    money disappears; ruff rule E722 is enabled to catch it.

Both variants are frozen dataclasses, so they carry `__match_args__` and work
directly in a `match` statement — which is how the orchestrator consumes them:

    match adjudicator.resolve(ambiguity):
        case Ok(verdict): ctx.accept(verdict.as_proposal())
        case Err(code):   ctx.flag(ReasonCode.ADJUDICATION_REJECTED, detail=code)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, NoReturn, TypeAlias, TypeVar

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")
F = TypeVar("F")


class UnwrapError(RuntimeError):
    """Unwrapped the wrong variant. Always a bug in the caller, never data."""


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """A successful outcome carrying a value."""

    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_err(self) -> NoReturn:
        raise UnwrapError(f"unwrap_err() on Ok({self.value!r})")

    def unwrap_or(self, default: T) -> T:
        return self.value

    def map(self, fn: Callable[[T], U]) -> Ok[U]:
        return Ok(fn(self.value))

    def map_err(self, fn: Callable[[E], F]) -> Ok[T]:
        return self

    def and_then(self, fn: Callable[[T], Ok[U] | Err[E]]) -> Ok[U] | Err[E]:
        return fn(self.value)


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    """An expected failure carrying a reason.

    The reason is data — it ends up on an exception card, so it must be
    meaningful to a controller, not a stack trace.
    """

    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self) -> NoReturn:
        raise UnwrapError(f"unwrap() on Err({self.error!r})")

    def unwrap_err(self) -> E:
        return self.error

    def unwrap_or(self, default: T) -> T:
        return default

    def map(self, fn: Callable[[T], U]) -> Err[E]:
        return self

    def map_err(self, fn: Callable[[E], F]) -> Err[F]:
        return Err(fn(self.error))

    def and_then(self, fn: Callable[[T], Ok[U] | Err[E]]) -> Err[E]:
        return self


Result: TypeAlias = Ok[T] | Err[E]
