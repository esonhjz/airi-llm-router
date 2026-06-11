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

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

settings = Settings()
