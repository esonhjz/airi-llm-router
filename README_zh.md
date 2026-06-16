# Airi LLM Router

[English](README.md) | [简体中文](README_zh.md) | [日本語](README_ja.md)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-00a393.svg)
![Architecture](https://img.shields.io/badge/Architecture-Asynchronous_Microservice-8a2be2.svg)

一个高并发、硬件感知的 LLM 推理流量调度网关。基于 FastAPI 和 `asyncio` 构建，作为前端客户端与本地 GPU 推理引擎（如 Ollama、vLLM）之间的中间层。

## 1. 系统架构

```mermaid
graph TD
    classDef client fill:#1e1e1e,stroke:#00e5ff,stroke-width:2px,color:#fff;
    classDef gateway fill:#161b22,stroke:#39ff14,stroke-width:2px,color:#fff;
    classDef engine fill:#2d0a28,stroke:#ff0055,stroke-width:2px,color:#fff;
    classDef queue fill:#0d1117,stroke:#ff6600,stroke-width:1px,stroke-dasharray: 5 5,color:#fff;

    Client["📱 Airi<br>(前端)"]:::client
    
    subgraph "Airi LLM Router"
        API["FastAPI /v1/chat/completions"]:::gateway
        Monitor{"VRAM 熔断器<br>(pynvml)"}:::gateway
        Classifier["负载分类器"]:::gateway
        
        subgraph "双轨优先级队列"
            Q_Fast["高速队列<br>(轻负载)"]:::queue
            Q_Batch["批处理队列<br>(重负载/视觉)"]:::queue
        end
        
        Workers["异步工作池<br>(有界并发)"]:::gateway
    end

    GPU["🖥️ 本地推理引擎"]:::engine

    Client -->|JSON| API
    API --> Monitor
    Monitor -- "VRAM > 85%" --> Drop["HTTP 429 (硬拦截)"]
    Monitor -- "VRAM > 75%" --> Throttle{"重负载?"}
    Throttle -- "是" --> Drop2["HTTP 429 (软节流)"]
    
    Drop -. "SmartFetch 退避重试" .-> Client
    Drop2 -. "SmartFetch 退避重试" .-> Client
    
    Monitor -- "< 75%" --> Classifier
    Throttle -- "否" --> Classifier
    
    Classifier -- "Tokens < 1024" --> Q_Fast
    Classifier -- "Tokens ≥ 1024 / Image" --> Q_Batch
    
    Q_Fast --> Workers
    Q_Batch --> Workers
    Workers -->|对齐并发度| GPU
```

## 2. 核心机制

### 2.1 硬件感知熔断器
后台守护进程通过 `pynvml` 以 1.0 秒为周期轮询 NVIDIA GPU，监控显存分配。
- **< 75%**：正常运行，流量全量放行。
- **75% - 85%**：软节流。拒绝重负载/多模态请求（返回 `HTTP 429`），允许轻量级请求通过。
- **> 85%**：硬熔断。拦截所有入站流量，并返回带有指数退避 `Retry-After` 头部的响应。

### 2.2 负载分类与特征提取
拦截兼容 OpenAI 格式的入站请求并进行分类（`LIGHTWEIGHT`、`HEAVY`、`MULTIMODAL`）。Base64 图片负载将被剥离并落盘，使用轻量级文件引用替代占用大量内存的数组，以节省网关内存。

### 2.3 双轨优先级路由
通过将负载分发至不同的队列，彻底消除队头阻塞（HoL Blocking）：
- **高速队列**：处理低延迟的对话请求。
- **批处理队列**：处理高计算成本的长文档/视觉任务。
具有严格容量限制的 `N` 个异步工作池（数量与 GPU 最大并行阈值对齐）负责消费队列，防止显存上下文抖动（Context Thrashing）。

---

## 3. 基准测试与性能验证

### 测试环境
- **GPU**: NVIDIA RTX 5070 Ti (16GB VRAM)
- **大模型引擎**: Qwen 2.5 (7B) 基于 Ollama
- **测试负载**: 150 个高并发混合请求（70% 轻量对话，30% 重型长文本）。

### 3.1 队头阻塞（HoL Blocking）消除验证
通过 `Payload Classifier` 将常规对话分流至 **High-Speed Queue（高速队列）**，轻量请求彻底绕过了重型任务的排队等待。

- **轻量级对话**: P95 响应延迟从 **14.6 秒**暴降至 **1.5 秒**（**时延消减 89.7%**）。
- **重型密集文本**: 响应时间稳定在 15.1s 至 17.2s 之间。

![时延消减对比图](tests/benchmarks/logs/vs_latency_reduction.png)

### 3.2 显存限制与并发背压生命周期
Ollama 原生采用显存预分配机制（稳定在 **62%** 基线），因此并发冲击不会体现在显存上涨上，而是完全转化为**推理排队延迟（Latency）**。

1. **队列堆积期 (0s - 12s)**：150 个请求轰炸网关，引擎内部缓存队列迅速饱和，导致响应延迟上扬，而物理显存保持在 62%。
2. **流控触发点 (13.1s)**：延迟逼近超时临界点，硬件熔断器在 75% WARNING 警戒线时间点后介入。
3. **精准负载舍弃**：网关拒绝放行后续超载任务，直接向客户端弹回 **HTTP 429 保护拦截**（红色 `X` 标记）。
4. **前端自适应闭环**：`airi-launcher.js` 注入的 `smartFetch` 拦截器捕获 429 信号并执行异步休眠（`setTimeout`）以推迟重试，同时抛出 `airi-vram-warning` 事件，使 UI 渲染黄色等待提示，避免红屏崩溃。

![显存背压控制图](tests/benchmarks/logs/vs_vram_backpressure.png)

---

## 4. 部署指南

### 环境依赖
- Docker & Docker Compose
- NVIDIA GPU 及驱动（需支持 `nvidia-smi`）
- Node.js (v18+)

### 第 1 步：目录结构要求
请确保 `airi-llm-router` 仓库与前端主仓库（`airi` 或旧版 `airi-companion`）被克隆在同一个父级目录下：
```text
parent-directory/
├── airi/                  # Airi 官方前端仓库 (或 airi-companion)
└── airi-llm-router/       # 本网关仓库
```

### 第 2 步：一键启动与无感代理 (Transparent Proxy)
Airi LLM Router 的设计理念是**无感代理**。我们提供了一个 NodeJS 启动器，它会自动嗅探相邻的前端代码库，对其网络层进行热补丁注入，使其能够优雅地处理 HTTP 429 回压警告，并**强制劫持所有发出的 LLM 请求至本地网关**。

**前端 UI 完全零配置，开箱即用。**

```bash
# 在 airi-llm-router 目录下执行
node airi-launcher.js
```

### 手动独立启动
如果你希望绕过启动器，单独部署本网关：
```bash
cp .env.example .env
docker compose up -d
```
