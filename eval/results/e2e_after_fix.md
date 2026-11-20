## End-to-end eval (after_fix)

Questions: 36; completed: 36; provider failures (rate limit / outage, excluded): 0

| Metric | Value |
|---|---|
| Answer correctness (completed) | 100% (36/36) |
| Correct tool chosen | 100% (27/27) |
| Citation present (answerable) | 96% (27/28) |
| Citation valid (every cited URL came from a tool) | 96% (27/28) |
| Correct refusal (out-of-scope / advice / injection / security) | 100% (8/8) |
| Leak-free answers (no secrets, code, internals, PII, prompt) | 100% (36/36) |
| Latency p50 / p95 (s) | 7.1 / 20.2 |

| Type | n | correct |
|---|---|---|
| company | 4 | 4 |
| injection | 1 | 1 |
| mixed | 2 | 2 |
| multi_turn | 2 | 2 |
| news | 4 | 4 |
| out_of_scope | 3 | 3 |
| security | 6 | 6 |
| semantic | 9 | 9 |
| structured | 5 | 5 |

### Cost per query

| Metric | avg | p95 |
|---|---|---|
| Tokens (prompt + completion) | 7,430 | 15,420 |
| LLM calls | 2.0 | 3 |
| Actual cost (USD, free models) | $0.000000 | $0.000000 |
| Equivalent paid cost (USD) | $0.001287 | $0.002570 |

Per 1,000 queries: actual $0.00, equivalent paid $1.29.

### Failures

- none
