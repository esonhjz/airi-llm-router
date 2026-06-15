# Airi LLM Router

A high-performance, asynchronous LLM routing gateway built with FastAPI and HTTPX. Designed to centralize and manage language model requests across the Airi ecosystem, it prevents connection exhaustion, protects GPU compute via queue backpressure, and provides dynamic multi-provider routing (Ollama, ModelScope, OpenAI) with extreme memory optimization for multi-modal requests.

## 🚀 Key Features

### 1. Industrial-Grade Reliability & Traffic Control
- **Async Queue & Backpressure**: All requests pass through an `asyncio.Queue` with bounded sizes (`queue_max_size=64`). Background workers limit active upstream connections (`queue_worker_count=4`), preventing GPU VRAM exhaustion or server crashing from sudden traffic spikes. Returns `429 Too Many Requests` instantly when saturated.
- **Exponential Backoff Retries**: Built-in resilience against transient upstream network jitters (`502`, `503`, `504`) and rate-limits (`429`). Respects the upstream `Retry-After` headers and strictly guards streaming requests to prevent corrupted SSE frames mid-stream.
- **Early Disconnect Protection**: Races HTTP requests against client disconnects. If the client drops early, the worker instantly tears down the upstream generation, saving precious compute.

### 2. Multi-Provider Dynamic Routing (Adapter Pattern)
- **Adapter Engine**: Easily mix-and-match LLM providers. Currently supports OpenAI-compatible endpoints (Ollama, vLLM) and Alibaba's DashScope/ModelScope (with native nested parameter and SSE header transformations).
- **Three-Tier Routing Strategy**: 
  1. **Prefix Matching**: Requesting `ms:qwen-vl-plus` seamlessly routes to ModelScope.
  2. **Config Mapping**: Set specific substring mappings via environment variables (e.g., `MODEL_ROUTES='{"qwen": "modelscope"}'`).
  3. **Fallback**: Routes to the default backend (e.g., local Ollama).

### 3. High-Concurrency Memory Defenses (Image Offloading)
- **Base64 Disk Offloading**: Extremely large multi-modal images (Base64) can bloat server RAM under high concurrency. The router automatically intercepts images larger than 10KB, offloads them to the OS temporary disk, and replaces them with lightweight pointers (`imgref:sha256...`) in the queue.
- **Global Deduplication & GC**: Identical images share the same disk file using SHA-256 fingerprinting. Strictly managed via reference counting (`_REFCOUNT`) to ensure temporary files are permanently deleted (`release_images`) the moment the task completes or fails.

### 4. Non-blocking VRAM Warmup
- **Startup Probe**: On launch, the `lifespan` initiates a background `max_tokens=1` probe to pre-load the default LLM into VRAM. It handles backoffs natively without blocking the FastAPI HTTP server from opening its ports.

## 📦 Getting Started

### Option A — Docker (Recommended)

Spin up the full stack (router + Ollama with GPU passthrough) in a single command:

```bash
# 1. Copy and edit the environment file
cp .env.example .env               # set LLM_DEFAULT_MODEL, ports, etc.

# 2. Start everything
docker compose up --build -d       # builds the router image, pulls Ollama

# 3. Pull a model into Ollama (first run only)
docker exec airi-ollama ollama pull AiriLocal

# 4. Verify
curl http://localhost:8000/health   # {"status":"healthy","queue":...,"warmup":"complete"}
```

> **No NVIDIA GPU?** Delete the `deploy:` block inside `docker-compose.yml` under the `ollama` service before running. Ollama will fall back to CPU mode automatically.

Stop and remove containers (model weights are preserved in the `ollama-data` volume):
```bash
docker compose down
```

Wipe everything including downloaded models:
```bash
docker compose down -v
```

---

### Option B — Manual Installation


   ```bash
   git clone <repository_url>
   cd airi-llm-router
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
   pip install -e .
   ```

3. Create a `.env` file in the root directory:
   ```env
   # Core setup
   LLM_BASE_URL=http://localhost:11434/v1
   LLM_API_KEY=ollama
   LLM_DEFAULT_MODEL=AiriLocal
   
   # Concurrency & Retries
   QUEUE_MAX_SIZE=64
   QUEUE_WORKER_COUNT=4
   UPSTREAM_MAX_RETRIES=3
   
   # ModelScope (DashScope) Integration
   MODELSCOPE_API_KEY=your_dashscope_api_key
   # Use explicit prefixes like "ms:qwen-vl-plus" instead of broad substring rules.
   # MODEL_ROUTES='{"gpt": "openai"}'
   ```

### Running the Router

Start the FastAPI server using Uvicorn:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

The server will be available at `http://localhost:8000`. 
Check gateway health, queue utilization, and warmup status at `http://localhost:8000/health`.

### Testing

Run the automated test suite using `pytest`. The tests leverage `respx` to mock upstream API responses and ensure that the global error interceptors, multi-modal payload assemblies, and ASGI lifespan functions execute accurately.

```bash
pip install pytest pytest-asyncio respx
pytest tests/ -v
```

## 🏗️ Architecture Overview

- **`src/main.py`**: Application entry point, non-blocking lifespan manager (warmup + connection pooling), and global error interceptors.
- **`src/config.py`**: Centralized configuration management using `pydantic-settings`.
- **`src/queue/worker.py`**: Consumer background tasks managing backpressure, upstream retries, image restoration, and graceful cancellation.
- **`src/router/dispatch.py`**: Core routing logic handling enqueueing, payload construction, and stream yielding.
- **`src/adapters/`**: Routing strategy engine and backend-specific payload normalizers (`base.py`, `registry.py`, `ollama.py`, `modelscope.py`).
- **`src/media/`**: Offloads incoming Base64 images to local temporary files to prevent JSON payload blobs from choking the RAM.
- **`src/monitor/`**: `vram.py` polls physical GPU memory via `pynvml` at 1.0s intervals, serving as the hardware-aware engine for dynamic backpressure.
- **`src/logger.py`**: Centralized JSON-structured logging engine for high-observability parsing (captures `vram_percent`, `event_type`, etc.).
- **`tests/benchmarks/`**: Official performance testing assets (e.g. `test_stress.py`) used to simulate concurrency spikes and validate the anti-OOM circuit breakers.

## Roadmap & Known Limitations
- **`tests/`**: Contains automated `pytest` test suites (`conftest.py` for ASGI simulation and routing logic tests).

## License

This project is licensed under the MIT License.
