from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Airi LLM Router"
    debug: bool = False

    # LLM upstream configuration (defaults to Ollama compatibility)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_default_model: str = "AiriLocal"

    # Global httpx.AsyncClient connection pool parameters
    pool_max_connections: int = 100
    pool_max_keepalive: int = 30
    pool_connect_timeout: float = 10.0
    pool_read_timeout: float = 300.0
    pool_write_timeout: float = 10.0

    # Feature toggles
    ollama_disable_think: bool = False

    # Async request queue parameters
    queue_max_size: int = 64
    queue_worker_count: int = 4

    # Classification thresholds
    classifier_heavy_max_tokens: int = 1024
    classifier_heavy_prompt_chars: int = 2048

    # Upstream retry policy
    # Retries apply only to transient faults (5xx, 429, connection errors).
    # Permanent client errors (4xx except 429) are never retried.
    upstream_max_retries: int = 3
    upstream_retry_base_delay: float = 0.5   # seconds; doubled on each attempt
    upstream_retry_max_delay: float = 8.0    # seconds; caps the exponential growth

    # Startup warmup — sends a minimal request to pre-load the model into VRAM.
    # Warmup failure is non-fatal; the gateway starts regardless.
    warmup_enabled: bool = True
    warmup_max_retries: int = 5
    warmup_retry_base_delay: float = 2.0     # seconds
    warmup_total_timeout: float = 120.0      # seconds; overall budget before giving up

    # Multi-backend adapter routing
    # default_adapter: which adapter to use when no prefix or route matches.
    # model_routes: JSON map of model-name substrings → adapter name, evaluated in order.
    #   Example (in .env): MODEL_ROUTES='{"qwen-vl": "modelscope", "llama": "ollama"}'
    default_adapter: str = "ollama"
    model_routes: dict[str, str] = Field(default_factory=dict)

    # ModelScope / DashScope backend
    modelscope_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    modelscope_api_key: str = ""

    # Multimodal image offloading
    # Base64 image data URLs larger than this threshold are written to a temp file;
    # only a lightweight 'imgref:{hash}' pointer is held in the queue.
    image_offload_enabled: bool = True
    image_offload_threshold: int = 10_240    # bytes (~7.5 KB of raw binary after decode)

    # GPU VRAM monitoring (requires nvidia-ml-py / pynvml).
    # When enabled, the gateway reads physical GPU memory at vram_poll_interval
    # and enforces a three-zone circuit breaker (safe / warning / danger).
    # If pynvml is missing or NVML init fails, the gateway silently falls back
    # to static queue thresholds — no crash, no false-positive blocks.
    vram_monitor_enabled: bool = True
    vram_gpu_index: int = 0                       # which GPU to watch (multi-GPU: pick primary)
    vram_poll_interval: float = 1.0               # seconds between NVML reads
    vram_threshold_warning: float = 75.0          # % — soft throttle starts
    vram_threshold_danger: float = 85.0           # % — hard circuit-break
    vram_max_probe_failures: int = 3              # consecutive NVML read failures before fallback
    vram_retry_after_min: int = 2                 # min Retry-After jitter (seconds)
    vram_retry_after_max: int = 5                 # max Retry-After jitter (seconds)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
