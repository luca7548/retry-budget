from __future__ import annotations

import threading
from typing import Callable, Optional


class BudgetExhausted(Exception):
    """Raised when a retry is attempted but the budget has no tokens left.

    Carrying the deficit lets the caller log how badly the budget was
    overspent, which is useful when diagnosing a dependency that is
    both slow to fail and retried aggressively.
    """

    def __init__(self, deficit: int) -> None:
        self.deficit = deficit
        super().__init__(f"retry budget exhausted; deficit={deficit}")


class RetryBudget:
    """A shared retry budget that limits how many retries a dependency may
    consume, decaying slowly so a recovered dependency can retry again.

    The budget is a token bucket with a cap and a linear refill rate.
    Every successful or first-attempt call deposits one token (capped at
    ``max_tokens``); every retry withdraws one. When the bucket is empty,
    retries are refused with :class:`BudgetExhausted`.

    Why a token bucket rather than a fixed counter: a hard counter resets
    catastrophically at the period boundary, letting a flapping dependency
    burst the full budget the instant the window rolls over. The bucket's
    continuous refill spreads recovery out, and its cap prevents unbounded
    accumulation during healthy periods.

    The clock is injected so tests are deterministic. Production code should
    pass ``time.monotonic``; tests pass a fake that advances explicitly.
    """

    def __init__(
        self,
        *,
        max_tokens: int,
        refill_rate: float,
        clock: Callable[[], float],
        initial_tokens: Optional[float] = None,
    ) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be > 0")
        if initial_tokens is not None and initial_tokens < 0:
            raise ValueError("initial_tokens must be >= 0")

        self._max_tokens = max_tokens
        self._refill_rate = refill_rate
        self._clock = clock
        self._tokens = float(initial_tokens) if initial_tokens is not None else float(max_tokens)
        self._last_refill = clock()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------
    def _refill_locked(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._max_tokens, self._tokens + elapsed * self._refill_rate)
            self._last_refill = now

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def can_retry(self) -> bool:
        """Return True if at least one retry token is available."""
        with self._lock:
            self._refill_locked()
            return self._tokens >= 1.0

    def try_retry(self) -> None:
        """Consume one retry token.

        Raises :class:`BudgetExhausted` if no token is available. The
        deficit reported is how far below zero the bucket would have gone,
        which is zero when the bucket was merely empty.
        """
        with self._lock:
            self._refill_locked()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            raise BudgetExhausted(deficit=1 - int(self._tokens))

    def deposit(self, n: int = 1) -> None:
        """Deposit ``n`` tokens, capped at ``max_tokens``.

        Called after a successful operation. A first attempt that succeeds
        deposits one token; a retry that succeeds also deposits one, so the
        net cost of a retried-but-successful call is zero. This is a
        deliberate choice: we want the budget to reflect *wasted* work, not
        total work.
        """
        if n <= 0:
            return
        with self._lock:
            self._refill_locked()
            self._tokens = min(self._max_tokens, self._tokens + n)

    @property
    def tokens(self) -> float:
        """Current token count, after refilling to the clock's now."""
        with self._lock:
            self._refill_locked()
            return self._tokens
