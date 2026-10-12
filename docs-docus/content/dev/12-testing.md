# Testing

**Source files:** `tests/` directory

## Test Structure

```
tests/
├── test_iq_foundations.py      # 18 IQ primitives
├── test_iq_prompt_primitives.py # registry + assembly
├── test_redis_retrieval.py      # memory, hash, INCR
├── test_kb_compiler.py          # full pipeline
└── test_call_*                  # integration
```

## Run Tests

\`\`\`bash
pytest tests/ -q                 # all tests
pytest tests/test_iq_foundations.py -v  # single file
pytest tests/ -k "memory" -v     # by keyword
\`\`\`

## Latency Gates

Latency tests enforce <5ms budgets (p95). If you add computation, you may exceed it.

## Related
- [Deployment](./13-deployment)
- [Architecture](./01-architecture)
