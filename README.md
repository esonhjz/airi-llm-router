# Airi LLM Router

[English](README.md) | [简体中文](README_zh.md) | [日本語](README_ja.md)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-00a393.svg)
![Architecture](https://img.shields.io/badge/Architecture-Asynchronous_Microservice-8a2be2.svg)

A highly concurrent, hardware-aware gateway for LLM inference orchestration. Built on FastAPI and `asyncio`, it mediates traffic between frontend clients and local GPU inference engines (e.g., Ollama, vLLM).

## 1. System Architecture

```mermaid
graph TD
    classDef client fill:#1e1e1e,stroke:#00e5ff,stroke-width:2px,color:#fff;
    classDef gateway fill:#161b22,stroke:#39ff14,stroke-width:2px,color:#fff;
    classDef engine fill:#2d0a28,stroke:#ff0055,stroke-width:2px,color:#fff;
    classDef queue fill:#0d1117,stroke:#ff6600,stroke-width:1px,stroke-dasharray: 5 5,color:#fff;

    Client["📱 Airi Companion<br>(Frontend)"]:::client
    
    subgraph "Airi LLM Router"
        API["FastAPI /v1/chat/completions"]:::gateway
        Monitor{"VRAM Circuit Breaker<br>(pynvml)"}:::gateway
        Classifier["Payload Classifier"]:::gateway
        
        subgraph "Dual-Track Queues"
            Q_Fast["High-Speed Queue<br>(Lightweight)"]:::queue
            Q_Batch["Batch Queue<br>(Heavy/Vision)"]:::queue
        end
        
        Workers["Async Workers<br>(Bounded Concurrency)"]:::gateway
    end

    GPU["🖥️ Local Inference Engine"]:::engine

    Client -->|JSON| API
    API --> Monitor
    Monitor -- "VRAM > 85%" --> Drop["HTTP 429 (Drop)"]
    Monitor -- "VRAM > 75%" --> Throttle{"Heavy Payload?"}
    Throttle -- "Yes" --> Drop2["HTTP 429 (Soft Throttle)"]
    
    Monitor -- "< 75%" --> Classifier
    Throttle -- "No" --> Classifier
    
    Classifier -- "Tokens < 1024" --> Q_Fast
    Classifier -- "Tokens ≥ 1024 / Image" --> Q_Batch
    
    Q_Fast --> Workers
    Q_Batch --> Workers
    Workers -->|Aligned Concurrency| GPU
```

## 2. Core Mechanisms

### 2.1 Hardware-Aware Circuit Breaker
A daemon polls the NVIDIA GPU via `pynvml` at 1.0s intervals to monitor VRAM allocation.
- **< 75%**: Normal operation.
- **75% - 85%**: Soft-throttling. Rejects heavy/multimodal payloads with `HTTP 429`; allows lightweight requests.
- **> 85%**: Hard circuit breaker. Halts incoming traffic with exponential backoff `Retry-After` headers.

### 2.2 Payload Classifier & Offloading
Intercepts incoming OpenAI-compatible payloads and classifies them (`LIGHTWEIGHT`, `HEAVY`, `MULTIMODAL`). Base64 image payloads are offloaded to disk, replacing memory-heavy arrays with lightweight file references to preserve gateway RAM.

### 2.3 Dual-Track Priority Routing
Resolves Head-of-Line (HoL) blocking by routing payloads into separate queues:
- **High-Speed Queue**: For low-latency conversational queries.
- **Batch Queue**: For computationally expensive document/vision tasks.
A strict pool of `N` asynchronous workers (aligned with the GPU's max parallel threshold) drains the queues, preventing context thrashing.

---

## 3. Benchmark

### Test Environment
- **GPU**: NVIDIA RTX 5070 Ti (16GB VRAM)
- **Model**: Qwen 2.5 (7B) via Ollama
- **Methodology**: 150 concurrent mixed payloads (lightweight + heavy) over 15 seconds.
- **Comparison**: v2 (Single Async FIFO Queue) vs. v3 (Dual Queues + VRAM Throttling).

### Head-of-Line Blocking Resolution
Routing lightweight requests through the High-Speed queue bypassed blocking from heavy document tasks. 
**Result**: 96.6% P95 latency reduction (from ~35s down to 1.2s).

![Latency Reduction](tests/benchmarks/logs/vs_latency_reduction.png)

### VRAM Backpressure Performance
Under heavy concurrent load (v2), unbounded queues led to severe request accumulation and client-side timeouts. In v3, the circuit breaker actively intercepted payloads when VRAM crossed 75%/85%, executing controlled load shedding (HTTP 429).
**Result**: VRAM allocation remained strictly bounded below the 85% Danger threshold, ensuring the underlying GPU never experienced Out-Of-Memory exceptions, while safe capacity (HTTP 200) was processed smoothly.

![VRAM Backpressure](tests/benchmarks/logs/vs_vram_backpressure.png)

---

## 4. Deployment

### Prerequisites
- Docker & Docker Compose
- NVIDIA GPU with drivers (`nvidia-smi` must be available)
- Node.js (v18+)

### Step 1: Directory Structure
Ensure that the `airi-llm-router` is cloned alongside the main frontend repository (either `airi` or `airi-companion`) in the same parent directory:
```text
parent-directory/
├── airi/                  # The official Airi frontend (or airi-companion)
└── airi-llm-router/       # This gateway repository
```

### Step 2: Bootstrapping & Transparent Proxy
Airi LLM Router acts as a **Transparent Proxy**. We provide a NodeJS launcher that automatically scans the adjacent frontend codebase, hot-patches the network layer to gracefully handle HTTP 429 backpressure warnings, and forcefully redirects all outgoing LLM requests to the local gateway. 

**Zero frontend UI configuration is required.**

```bash
# Inside the airi-llm-router directory
node airi-launcher.js
```

### Manual Boot (Standalone Mode)
If you wish to bypass the launcher and deploy the gateway independently:
```bash
cp .env.example .env
docker compose up -d
```
