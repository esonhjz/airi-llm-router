from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

class ContentPartText(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ImageUrl(BaseModel):
    url: str

class ContentPartImage(BaseModel):
    type: Literal["image_url"] = "image_url"
    image_url: ImageUrl

ContentPart = Annotated[Union[ContentPartText, ContentPartImage], Field(discriminator="type")]

class ChatMessage(BaseModel):
    role: str
    content: str | list[ContentPart]

class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible Chat Completions request model."""
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    think: bool | None = None  # Ollama specific extension
