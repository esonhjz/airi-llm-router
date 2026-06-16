import asyncio
import httpx
import time
import numpy as np
import random
import json
import argparse
import datetime

URL = "http://localhost:8000/v1/chat/completions"
HEALTH_URL = "http://localhost:8000/health"

MASSIVE_CONTEXT = "The quick brown fox jumps over the lazy dog. " * 50  

vram_history = []
stop_polling = False

def get_payload(req_type="LIGHTWEIGHT"):
    if req_type == "HEAVY":
        return {
            "model": "qwen2.5:7b",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant. Please read this long text carefully."},
                {"role": "user", "content": f"{MASSIVE_CONTEXT}\n\n---\nSummarize. ID: {random.randint(1, 100000)}"}
            ],
            "max_tokens": 512,
            "stream": False
        }
    else:
        return {
            "model": "qwen2.5:7b",
            "messages": [
                {"role": "user", "content": f"Hi! ID: {random.randint(1, 100000)}"}
            ],
            "max_tokens": 10,
            "stream": False
        }

async def poll_vram():
    global stop_polling
    async with httpx.AsyncClient() as client:
        while not stop_polling:
            try:
                resp = await client.get(HEALTH_URL)
                data = resp.json()
                vram_pct = data.get("gpu", {}).get("usage_pct", 0.0)
                vram_history.append((time.time(), vram_pct))
            except Exception:
                vram_history.append((time.time(), 0.0))
            await asyncio.sleep(0.1)

def get_vram_at(t):
    if not vram_history:
        return 0.0
    # find closest vram snapshot
    closest = min(vram_history, key=lambda x: abs(x[0] - t))
    return closest[1]

async def fire_request(client: httpx.AsyncClient, req_id: int, req_type: str, results: list, log_file):
    start_time = time.time()
    event_type = "request_processed"
    status_code = 500
    try:
        response = await client.post(URL, json=get_payload(req_type), timeout=120.0)
        status_code = response.status_code
        elapsed = time.time() - start_time
        
        if status_code == 429:
            event_type = "soft_throttle"
            
        results.append({"id": req_id, "status": status_code, "time": elapsed})
    except Exception as e:
        elapsed = time.time() - start_time
        status_code = 500
        event_type = "error"
        results.append({"id": req_id, "status": type(e).__name__, "time": elapsed})

    # Write to log immediately
    log_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "vram_percent": get_vram_at(time.time()),
        "event_type": event_type,
        "status": status_code,
        "latency_s": round(elapsed, 3),
        "req_type": req_type
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_data) + "\n")

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=str, default="stress_metrics.jsonl")
    args = parser.parse_args()
    
    # clear log file
    with open(args.log, "w", encoding="utf-8") as f:
        pass
        
    print(f"🚀 Preparing to fire requests. Logs will be saved to {args.log}")
    results = []
    
    global stop_polling
    poller = asyncio.create_task(poll_vram())
    
    limits = httpx.Limits(max_connections=600, max_keepalive_connections=600)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = []
        for i in range(150):
            req_type = "HEAVY" if random.random() < 0.7 else "LIGHTWEIGHT"
            tasks.append(asyncio.create_task(fire_request(client, i, req_type, results, args.log)))
            if i % 10 == 0 and i > 0:
                await asyncio.sleep(1.0)  

        await asyncio.gather(*tasks)
    
    stop_polling = True
    await poller
    
    print("\n✅ Stress test completed!")
    successes = [r for r in results if r["status"] == 200]
    rate_limits = [r for r in results if r["status"] == 429]
    errors = [r for r in results if r["status"] not in (200, 429)]
    
    print(f"Total Requests: 150")
    print(f"Success (200): {len(successes)}")
    print(f"Throttled (429): {len(rate_limits)}")
    print(f"Errors/Timeouts: {len(errors)}")

if __name__ == "__main__":
    asyncio.run(main())