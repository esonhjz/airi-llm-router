from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings
from src.router.dispatch import router as dispatch_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages the global async HTTP connection pool to prevent frequent reconnections."""
    pool_limits = httpx.Limits(
        max_connections=settings.pool_max_connections,
        max_keepalive_connections=settings.pool_max_keepalive,
    )
    timeout = httpx.Timeout(
        connect=settings.pool_connect_timeout,
        read=settings.pool_read_timeout,
        write=settings.pool_write_timeout,
        pool=settings.pool_connect_timeout,
    )

    client = httpx.AsyncClient(
        limits=pool_limits,
        timeout=timeout,
        http2=True,
        follow_redirects=True,
    )
    app.state.http_client = client

    print(f"[Lifespan] 🚀 Connection pool ready (max_conn={settings.pool_max_connections}, http2=True)")
    yield
    await client.aclose()
    print("[Lifespan] 🛑 Connection pool released")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dispatch_router)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
