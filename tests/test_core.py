import math
import unittest

from retry_budget import RetryBudget, BudgetExhausted


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestConstruction(unittest.TestCase):
    def test_rejects_zero_max_tokens(self):
        with self.assertRaises(ValueError):
            RetryBudget(max_tokens=0, refill_rate=1.0, clock=FakeClock())

    def test_rejects_negative_max_tokens(self):
        with self.assertRaises(ValueError):
            RetryBudget(max_tokens=-5, refill_rate=1.0, clock=FakeClock())

    def test_rejects_zero_refill_rate(self):
        with self.assertRaises(ValueError):
            RetryBudget(max_tokens=10, refill_rate=0.0, clock=FakeClock())

    def test_rejects_negative_refill_rate(self):
        with self.assertRaises(ValueError):
            RetryBudget(max_tokens=10, refill_rate=-1.0, clock=FakeClock())

    def test_rejects_negative_initial_tokens(self):
        with self.assertRaises(ValueError):
            RetryBudget(
                max_tokens=10, refill_rate=1.0, clock=FakeClock(), initial_tokens=-1
            )

    def test_defaults_to_full_bucket(self):
        b = RetryBudget(max_tokens=5, refill_rate=1.0, clock=FakeClock())
        self.assertAlmostEqual(b.tokens, 5.0)

    def test_respects_initial_tokens(self):
        b = RetryBudget(
            max_tokens=5, refill_rate=1.0, clock=FakeClock(), initial_tokens=2
        )
        self.assertAlmostEqual(b.tokens, 2.0)


class TestRetry(unittest.TestCase):
    def test_can_retry_when_tokens_available(self):
        b = RetryBudget(max_tokens=3, refill_rate=1.0, clock=FakeClock())
        self.assertTrue(b.can_retry())

    def test_cannot_retry_when_empty(self):
        clk = FakeClock()
        b = RetryBudget(max_tokens=2, refill_rate=1.0, clock=clk)
        b.try_retry()
        b.try_retry()
        self.assertFalse(b.can_retry())

    def test_try_retry_consumes_token(self):
        b = RetryBudget(max_tokens=3, refill_rate=1.0, clock=FakeClock())
        b.try_retry()
        self.assertAlmostEqual(b.tokens, 2.0)

    def test_try_retry_raises_when_empty(self):
        b = RetryBudget(max_tokens=1, refill_rate=1.0, clock=FakeClock())
        b.try_retry()
        with self.assertRaises(BudgetExhausted):
            b.try_retry()

    def test_exhaustion_reports_zero_deficit_at_boundary(self):
        b = RetryBudget(max_tokens=1, refill_rate=1.0, clock=FakeClock())
        b.try_retry()
        try:
            b.try_retry()
            self.fail("expected BudgetExhausted")
        except BudgetExhausted as e:
            self.assertEqual(e.deficit, 1)

    def test_exhausted_does_not_consume(self):
        b = RetryBudget(max_tokens=1, refill_rate=1.0, clock=FakeClock())
        b.try_retry()
        with self.assertRaises(BudgetExhausted):
            b.try_retry()
        self.assertAlmostEqual(b.tokens, 0.0)


class TestDeposit(unittest.TestCase):
    def test_deposit_adds_token(self):
        b = RetryBudget(max_tokens=3, refill_rate=1.0, clock=FakeClock())
        b.try_retry()
        b.deposit()
        self.assertAlmostEqual(b.tokens, 3.0)

    def test_deposit_capped_at_max(self):
        b = RetryBudget(max_tokens=3, refill_rate=1.0, clock=FakeClock())
        b.deposit(10)
        self.assertAlmostEqual(b.tokens, 3.0)

    def test_deposit_zero_is_noop(self):
        b = RetryBudget(max_tokens=3, refill_rate=1.0, clock=FakeClock())
        b.deposit(0)
        self.assertAlmostEqual(b.tokens, 3.0)

    def test_deposit_negative_is_noop(self):
        b = RetryBudget(max_tokens=3, refill_rate=1.0, clock=FakeClock())
        b.deposit(-5)
        self.assertAlmostEqual(b.tokens, 3.0)

    def test_deposit_after_partial_drain(self):
        b = RetryBudget(max_tokens=5, refill_rate=1.0, clock=FakeClock())
        b.try_retry()
        b.try_retry()
        b.deposit()
        self.assertAlmostEqual(b.tokens, 4.0)


class TestRefill(unittest.TestCase):
    def test_refill_restores_tokens_over_time(self):
        clk = FakeClock()
        b = RetryBudget(max_tokens=2, refill_rate=1.0, clock=clk)
        b.try_retry()
        b.try_retry()
        self.assertFalse(b.can_retry())
        clk.advance(1.0)
        self.assertTrue(b.can_retry())

    def test_refill_capped_at_max(self):
        clk = FakeClock()
        b = RetryBudget(max_tokens=2, refill_rate=10.0, clock=clk)
        b.try_retry()
        clk.advance(100.0)
        self.assertAlmostEqual(b.tokens, 2.0)

    def test_refill_rate_scales_tokens(self):
        clk = FakeClock()
        b = RetryBudget(max_tokens=10, refill_rate=2.0, clock=clk)
        b.try_retry()  # 9 left
        b.try_retry()  # 8 left
        clk.advance(0.5)  # +1.0
        self.assertAlmostEqual(b.tokens, 9.0)

    def test_no_refill_when_clock_unchanged(self):
        clk = FakeClock()
        b = RetryBudget(max_tokens=5, refill_rate=1.0, clock=clk)
        b.try_retry()
        self.assertAlmostEqual(b.tokens, 4.0)
        # Reading again without advancing must not add tokens.
        self.assertAlmostEqual(b.tokens, 4.0)

    def test_refill_handles_clock_going_backward_gracefully(self):
        clk = FakeClock(start=10.0)
        b = RetryBudget(max_tokens=5, refill_rate=1.0, clock=clk)
        b.try_retry()
        clk.t = 5.0  # clock moved backward
        # Must not raise; must not add tokens.
        self.assertAlmostEqual(b.tokens, 4.0)


class TestInteraction(unittest.TestCase):
    def test_deposit_then_retry(self):
        b = RetryBudget(max_tokens=2, refill_rate=1.0, clock=FakeClock())
        b.try_retry()
        b.try_retry()
        with self.assertRaises(BudgetExhausted):
            b.try_retry()
        b.deposit()
        b.try_retry()  # should succeed now
        self.assertAlmostEqual(b.tokens, 0.0)

    def test_steady_state_success_keeps_budget_full(self):
        b = RetryBudget(max_tokens=5, refill_rate=1.0, clock=FakeClock())
        for _ in range(20):
            b.deposit()  # success
        self.assertAlmostEqual(b.tokens, 5.0)

    def test_retried_success_is_net_zero(self):
        b = RetryBudget(max_tokens=5, refill_rate=1.0, clock=FakeClock())
        before = b.tokens
        b.try_retry()  # retry happens
        b.deposit()    # retry succeeds
        self.assertAlmostEqual(b.tokens, before)


if __name__ == "__main__":
    unittest.main()
