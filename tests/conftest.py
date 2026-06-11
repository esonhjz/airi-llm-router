import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

@pytest.fixture
async def async_client():
    """
    Creates an asynchronous virtual client that simulates the FastAPI ASGI lifespan.
    This ensures that resources like the global HTTP connection pool are correctly
    initialized before tests run and torn down afterwards.
    """
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
