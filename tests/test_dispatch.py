import pytest
import httpx
from unittest.mock import patch
from src.config import settings

from src.router.schemas import ChatCompletionRequest, ChatMessage, ContentPartText, ContentPartImage, ImageUrl
from src.router.dispatch import build_upstream_payload

def test_build_upstream_payload_multimodal():
    """
    Logic test: Verifies that the Pydantic models correctly assemble multimodal data.
    """
    req = ChatCompletionRequest(
        model="test-model",
        messages=[
            ChatMessage(
                role="user",
                content=[
                    ContentPartText(type="text", text="Look at this image"),
                    ContentPartImage(
                        type="image_url", 
                        image_url=ImageUrl(url="data:image/png;base64,abcdefg")
                    )
                ]
            )
        ],
        stream=False,
        temperature=0.7,
        top_p=0.9
    )
    
    payload = build_upstream_payload(req)
    
    assert payload["model"] == "test-model"
    assert payload["stream"] is False
    assert payload["temperature"] == 0.7
    assert payload["top_p"] == 0.9
    assert len(payload["messages"]) == 1
    
    message = payload["messages"][0]
    assert message["role"] == "user"
    assert isinstance(message["content"], list)
    assert len(message["content"]) == 2
    
    part1, part2 = message["content"]
    assert part1["type"] == "text"
    assert part1["text"] == "Look at this image"
    assert part2["type"] == "image_url"
    assert part2["image_url"]["url"] == "data:image/png;base64,abcdefg"


import respx

@pytest.mark.asyncio
async def test_global_404_interceptor(async_client):
    """
    Mock interceptor test: Verifies that the HTTPStatusError interceptor in main.py works.
    When the upstream throws a 404 error (non-standard JSON), it should be transformed
    into a standard OpenAI error JSON format before being returned.
    """
    with respx.mock(assert_all_called=False) as respx_mock:
        # Intercept the request to the upstream LLM API
        upstream_url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        respx_mock.post(upstream_url).mock(
            return_value=httpx.Response(
                status_code=404,
                text="model 'qwen-not-exist' not found"
            )
        )

        response = await async_client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen-not-exist",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False
            }
        )
        
        # 1. Assert the status code is passed through as 404
        assert response.status_code == 404
        
        # 2. Assert the interceptor successfully transformed the text into standard OpenAI error structure
        data = response.json()
        assert "error" in data
        
        error_block = data["error"]
        assert error_block["type"] == "api_error"
        assert error_block["code"] == "upstream_api_error"
        assert "model 'qwen-not-exist' not found" in error_block["message"]
