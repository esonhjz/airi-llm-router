from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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


@app.exception_handler(httpx.RequestError)
async def upstream_connection_error_handler(request: Request, exc: httpx.RequestError):
    """Handles connection errors to the upstream LLM provider."""
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "message": f"Failed to connect to upstream LLM provider: {str(exc)}",
                "type": "server_error",
                "param": None,
                "code": "upstream_connection_failed"
            }
        }
    )


@app.exception_handler(httpx.HTTPStatusError)
async def upstream_status_error_handler(request: Request, exc: httpx.HTTPStatusError):
    """Handles HTTP errors returned by the upstream LLM provider."""
    status_code = exc.response.status_code
    
    try:
        upstream_error = exc.response.json()
        if "error" in upstream_error:
            return JSONResponse(status_code=status_code, content=upstream_error)
    except Exception:
        pass
        
    error_msg = exc.response.text or str(exc)
        
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": f"Upstream LLM error: {error_msg}",
                "type": "api_error",
                "param": None,
                "code": "upstream_api_error"
            }
        }
    )


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
