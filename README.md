# Airi LLM Router

A high-performance, asynchronous LLM routing gateway built with FastAPI and HTTPX. Designed to centralize and manage language model requests across the Airi ecosystem, it prevents connection exhaustion, protects GPU compute via queue backpressure, and provides dynamic multi-provider routing (Ollama, ModelScope, OpenAI) with extreme memory optimization for multi-modal requests.

## 🚀 Key Features

### 1. Dual-Lane Priority Queues & Intelligent Classification
- **Smart Feature Extraction**: `classifier.py` instantly analyzes incoming payloads to classify requests into `LIGHTWEIGHT` (pure short text), `HEAVY` (long context), or `MULTIMODAL` (Base64 images).
- **Dual-Track Dispatching**: Abandons traditional single-queue FIFO bottlenecks. Requests are dynamically routed into `high_speed_queue` (VIP lane) or `batch_queue` (Heavy lane). This entirely eliminates head-of-line blocking, ensuring quick chat prompts aren't starved by massive context requests.
- **QueueFull Backpressure**: Bounded queue sizes (`queue_max_size=64`) serve as the first line of defense against DDoS bursts, returning instant `429`s when the waiting room is saturated.

### 2. Hardware-Aware VRAM Circuit Breaker (Anti-OOM)
- **Async NVML Probe**: `vram.py` runs a non-blocking background coroutine polling real-time physical GPU memory every `1.0s` via `pynvml`.
- **Three-Tier Defense System**: 
  - 🟢 **SAFE (<75%)**: Full passthrough throughput.
  - 🟡 **WARNING (75%-85%)**: Adaptive Soft-Throttling. Aggressively rejects `HEAVY` and `MULTIMODAL` requests while keeping the `high_speed_queue` open for lightweight chats, squeezing maximum value out of remaining VRAM.
  - 🔴 **DANGER (>85%)**: Hard physical circuit break. Unconditionally drops all incoming traffic with `429 Too Many Requests` + `Retry-After` headers, physically preventing Linux OOM Killers.

### 3. Industrial-Grade Reliability & Observability
- **JSON Structured Auditing**: All interceptions, circuit breaks, and dispatches are logged via `logger.py` into high-observability, single-line JSON format (capturing `vram_percent`, `event_type`, `client_ip`), seamlessly integrating with ELK/Filebeat stacks.
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

### Option A — Docker (Gateway Only)

Spin up the containerized router using Docker Compose. This assumes your Ollama instance is already running natively on the host machine.

```bash
# 1. Copy and edit the environment file
cp .env.example .env

# 2. Start the gateway
docker compose up --build -d

# 3. Verify
curl http://localhost:8000/health
```

> **🚨 CRITICAL ARCHITECTURE WARNING:** 
> 1. **Physical Isolation**: The container is strictly locked down with hard quotas (`cpus: 2.0`, `mem_limit: 4G`) to prevent gateway memory leaks from bleeding into the host.
> 2. **1:1 Concurrency Alignment**: You **MUST** set `OLLAMA_NUM_PARALLEL=4` in your host's Ollama environment variables. This creates a perfect 1:1 physical closure with the gateway's `QUEUE_WORKER_COUNT=4`, preventing secondary queueing or unseen VRAM escape within the Ollama engine itself.
> 3. **GPU Passthrough**: Ensure your Docker engine supports NVIDIA capabilities (`deploy.resources.reservations.devices`) so the VRAM probe is not blinded.

Stop and remove the container:
```bash
docker compose down
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
- **`src/logger.py`**: Industrial-grade structured logging engine. Outputs all gateway actions, throttles, and hardware metrics as single-line JSON audit logs, ready for seamless ELK/Filebeat aggregation.
- **`tests/benchmarks/`**: Official performance testing assets (e.g. `test_stress.py`) used to simulate concurrency spikes and validate the anti-OOM circuit breakers.

## Roadmap & Known Limitations
- **`tests/`**: Contains automated `pytest` test suites (`conftest.py` for ASGI simulation and routing logic tests).

## License

This project is licensed under the MIT License.
