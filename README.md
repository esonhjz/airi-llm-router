# Airi LLM Router

A high-performance, asynchronous LLM routing gateway built with FastAPI and HTTPX. Designed to centralize and manage language model requests across the Airi ecosystem, preventing connection exhaustion and configuration drift.

## Features

- **Centralized Routing**: Consolidates LLM requests from multiple upstream services (Stage UI, Telegram Bot, Minecraft Bot, etc.) into a single unified endpoint.
- **Connection Pooling**: Utilizes a global `httpx.AsyncClient` with HTTP/2 multiplexing to manage persistent connections efficiently under high concurrency.
- **OpenAI Compatible**: Exposes a standard `/v1/chat/completions` endpoint.
- **Multi-modal Support**: Seamlessly proxies multi-modal content (e.g., base64 `image_url` data).
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

## Architecture Overview

- **`src/main.py`**: Application entry point and lifespan manager (initializes the global HTTP connection pool).
- **`src/config.py`**: Centralized configuration management using `pydantic-settings`.
- **`src/router/dispatch.py`**: Core routing logic and upstream request building.
- **`src/router/schemas.py`**: Pydantic data models enforcing OpenAI format compliance.

## License

This project is licensed under the MIT License.
