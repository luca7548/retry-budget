# Retry Budget

A shared retry budget for Python: a dependency that is failing cannot
amplify load by retrying without limit, because every retry consumes a
token from a bucket that only refills as the dependency demonstrates
health.

## Usage

```python
from retry_budget import RetryBudget, BudgetExhausted
import time

budget = RetryBudget(
    max_tokens=10,
    refill_rate=1.0,          # one token per second
    clock=time.monotonic,
)

def call_dependency():
    try:
        result = do_work()
        budget.deposit()       # success feeds the bucket
        return result
    except TransientError:
        budget.try_retry()     # raises BudgetExhausted if over budget
        return call_dependency()
```

## Why

When a downstream dependency starts failing, naive retry logic turns a
modest error rate into a load spike: every failed call is retried one or
more times, so the dependency sees N+1 attempts for every N requests.
If the dependency is already struggling, this kills it.

A retry budget caps the *number of retries*, not the number of calls.
First attempts are always allowed; only retries cost tokens. The bucket
refills continuously at a fixed rate and is capped, so a recovered
dependency earns back retry capacity gradually rather than all at once
at a window boundary.

The trade-off: during a sustained outage, legitimate retries are
refused even if they might have succeeded. This is intentional. It is
better to fail fast upstream than to let a failing dependency drag the
caller down with it.

## Edge cases

- **Clock moving backward** is ignored. The refill calculation only adds
  tokens when `now - last_refill > 0`, so a monotonic clock that jumps
  backward (NTP step, fake clock misuse) does not drain or freeze the
  bucket.

- **Deposit is capped.** A long healthy period does not let the bucket
  grow beyond `max_tokens`, so the first outage after recovery cannot
  burn a huge accumulated surplus.

- **`BudgetExhausted.deficit`** is `1` when the bucket was exactly
  empty. It does not go negative, because a refused retry does not
  consume anything. The field exists so callers can log how far the
  budget was overspent in aggregate, not per-call.

## Running the tests

```
PYTHONPATH=src python -m unittest discover -s tests
```
