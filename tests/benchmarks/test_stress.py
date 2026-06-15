import asyncio
import httpx
import time
import numpy as np

URL = "http://localhost:8000/v1/chat/completions"
CONCURRENCY = 500

import random

# Generate a massive dummy context to force KV cache expansion
# This will push the request into the HEAVY category and consume VRAM
# (Kept at * 50 to avoid crashing Ollama's num_ctx allocation completely)
MASSIVE_CONTEXT = "The quick brown fox jumps over the lazy dog. " * 50  

def get_payload():
    return {
        "model": "qwen2.5:7b",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Please read this long text carefully."},
            {"role": "user", "content": f"{MASSIVE_CONTEXT}\n\n---\nSummarize the above in 3 bullet points. Random ID: {random.randint(1, 100000)}"}
        ],
        "max_tokens": 512,
        "stream": False
    }

async def fire_request(client: httpx.AsyncClient, req_id: int, results: list):
    start_time = time.time()
    try:
        response = await client.post(URL, json=get_payload(), timeout=120.0)
        elapsed = time.time() - start_time
        results.append({"id": req_id, "status": response.status_code, "time": elapsed})
    except Exception as e:
        results.append({"id": req_id, "status": type(e).__name__, "time": 0})

async def main():
    print(f"🚀 Preparing to fire requests continuously to build up VRAM...")
    results = []
    
    limits = httpx.Limits(max_connections=600, max_keepalive_connections=600)
    async with httpx.AsyncClient(limits=limits) as client:
        # Instead of 500 instantly, we send 10 requests per second for 15 seconds
        # This gives VRAM time to rise, and subsequent requests will hit the VRAM throttle!
        tasks = []
        for i in range(150):
            tasks.append(asyncio.create_task(fire_request(client, i, results)))
            if i % 10 == 0 and i > 0:
                await asyncio.sleep(1.0)  # wait 1 second every 10 requests

        await asyncio.gather(*tasks)
    
    print("\n✅ Stress test completed! Generating report...")
    successes = [r for r in results if r["status"] == 200]
    rate_limits = [r for r in results if r["status"] == 429]
    errors = [r for r in results if r["status"] not in (200, 429)]
    
    print(f"Total Requests: {CONCURRENCY}")
    print(f"Success (200): {len(successes)}")
    print(f"Throttled (429): {len(rate_limits)}")
    print(f"Errors/Timeouts: {len(errors)}")

    if successes:
        times = [r["time"] for r in successes]
        print(f"\n[Success Metrics]")
        print(f"Min Latency:  {min(times):.2f}s")
        print(f"Max Latency:  {max(times):.2f}s")
        print(f"Avg Latency:  {sum(times)/len(times):.2f}s")
        print(f"P95 Latency:  {np.percentile(times, 95):.2f}s")
        print(f"P99 Latency:  {np.percentile(times, 99):.2f}s")

if __name__ == "__main__":
    asyncio.run(main())