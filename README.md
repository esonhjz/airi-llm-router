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

### Prerequisites
- Python 3.11+
- An upstream LLM provider (e.g., [Ollama](https://ollama.com/) running locally or ModelScope API Key)

### Installation

1. Clone the repository and navigate to the project directory:
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
   MODEL_ROUTES='{"qwen": "modelscope", "llama": "ollama"}'
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
- **`src/media/offload.py`**: Disk-backed memory optimization and reference counting garbage collection for Base64 streams.

## License

This project is licensed under the MIT License.
