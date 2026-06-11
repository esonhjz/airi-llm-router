from typing import Any

from pydantic import BaseModel, Field

class ContentPartText(BaseModel):
    type: str = "text"
    text: str

class ImageUrl(BaseModel):
    url: str

class ContentPartImage(BaseModel):
    type: str = "image_url"
    image_url: ImageUrl

class ChatMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]]

class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible Chat Completions request model."""
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    think: bool | None = None  # Ollama specific extension
