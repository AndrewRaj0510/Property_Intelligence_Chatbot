## End-to-end eval (latency_fix_q)

Questions: 3; completed: 3; provider failures (rate limit / outage, excluded): 0

| Metric | Value |
|---|---|
| Answer correctness (completed) | 100% (3/3) |
| Correct tool chosen | 100% (2/2) |
| Citation present (answerable) | 100% (2/2) |
| Citation valid (every cited URL came from a tool) | 100% (2/2) |
| Correct refusal (out-of-scope / advice / injection / security) | 100% (1/1) |
| Leak-free answers (no secrets, code, internals, PII, prompt) | 100% (3/3) |
| Latency p50 / p95 (s) | 5.7 / 16.5 |

| Type | n | correct |
|---|---|---|
| news | 1 | 1 |
| out_of_scope | 1 | 1 |
| structured | 1 | 1 |

### Cost per query

| Metric | avg | p95 |
|---|---|---|
| Tokens (prompt + completion) | 9,201 | 9,793 |
| LLM calls | 2.0 | 2 |
| Actual cost (USD, free models) | $0.000000 | $0.000000 |
| Equivalent paid cost (USD) | $0.001385 | $0.001584 |

Per 1,000 queries: actual $0.00, equivalent paid $1.39.

### Failures

- none
