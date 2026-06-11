# Airi LLM Router

A high-performance, asynchronous LLM routing gateway built with FastAPI and HTTPX. Designed to centralize and manage language model requests across the Airi ecosystem, preventing connection exhaustion, configuration drift, and providing production-grade error handling.

## Features

- **Centralized Routing**: Consolidates LLM requests from multiple upstream services (Stage UI, Telegram Bot, Minecraft Bot, etc.) into a single unified endpoint.
- **Connection Pooling**: Utilizes a global `httpx.AsyncClient` with HTTP/2 multiplexing via FastAPI's `lifespan` to manage persistent connections efficiently under high concurrency.
- **OpenAI Compatible API**: Exposes a standard `/v1/chat/completions` endpoint for both input and error outputs.
- **Production-Grade Error Interception**: 
  - Automatically catches network connection failures (`503` upstream failures) and translates them into OpenAI-compliant JSON error blocks.
  - Intercepts streaming failures inline and gracefully yields standard error blocks without breaking the SSE protocol.
  - Automatically maps `404` upstream model errors into standard `invalid_request_error`.
- **Strict Multi-modal Support**: Seamlessly proxies multi-modal content (e.g., base64 `image_url` data). Uses strict Pydantic discriminated unions (`Literal` + `discriminator`) to prevent type confusion attacks or malformed payload crashes.
- **Streaming & Non-Streaming**: Native support for Server-Sent Events (SSE) streaming and standard JSON responses.

## Getting Started

### Prerequisites

- Python 3.11+
- An upstream LLM provider (e.g., [Ollama](https://ollama.com/) running locally)

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

3. Create a `.env` file in the root directory (optional, defaults to local Ollama):
   ```env
   LLM_BASE_URL=http://localhost:11434/v1
   LLM_API_KEY=ollama
   LLM_DEFAULT_MODEL=AiriLocal
   ```

### Running the Router

Start the FastAPI server using Uvicorn:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

The server will be available at `http://localhost:8000`. You can explore the interactive API documentation at `http://localhost:8000/docs`.

### Testing

Run the automated test suite using `pytest`. The tests leverage `respx` to mock upstream API responses and ensure that the global error interceptors, multi-modal payload assemblies, and ASGI lifespan functions execute accurately.

```bash
pip install pytest pytest-asyncio respx
pytest tests/ -v
```

## Architecture Overview

- **`src/main.py`**: Application entry point, lifespan manager (initializes the global HTTP connection pool), and global error interceptor for upstream API exceptions.
- **`src/config.py`**: Centralized configuration management using `pydantic-settings`.
- **`src/router/dispatch.py`**: Core routing logic, upstream request building, and inline stream error handling.
- **`src/router/schemas.py`**: Strict Pydantic data models enforcing OpenAI format compliance and discriminated union checks.
- **`tests/`**: Contains automated test suites (`conftest.py` for ASGI lifespan simulation and `test_dispatch.py` for routing logic).

## License

This project is licensed under the MIT License.
