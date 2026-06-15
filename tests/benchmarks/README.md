# Benchmarks & Load Testing

This directory contains the scripts and assets used to perform concurrency and load testing on the Airi LLM Router.

## Contents
- `test_stress.py`: An asynchronous stress-test script that launches hundreds of concurrent requests against the router to trigger VRAM circuit breaking, soft-throttling, and OOM-prevention mechanisms.

## How to run the Stress Test

1. Ensure the gateway is running (`docker compose up -d`).
2. Activate your virtual environment and install dependencies (`httpx`, `asyncio`).
3. Run the script:

```bash
python tests/benchmarks/test_stress.py
```

The script will dump a massive concurrency spike onto the router. Check the router logs to observe the `soft_throttle` and `hard_throttle` JSON logs firing as the VRAM crosses the configured limits.
