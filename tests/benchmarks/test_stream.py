import httpx
import json

url = "http://localhost:8000/v1/chat/completions"
payload = {
    "model": "qwen2.5:7b",
    "messages": [{"role": "user", "content": "Tell me the formation of a black hole in detail."}],
    "stream": True
}

print("🚀 Connecting to Airi-LLM-Router ...\n")

with httpx.stream("POST", url, json=payload, timeout=120.0) as response:
    for line in response.iter_lines():
        if line.startswith("data: "):
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                content = chunk["choices"][0]["delta"].get("content", "")
                print(content, end="", flush=True)
            except Exception:
                pass

print("\n\n✅ Generation completed!")