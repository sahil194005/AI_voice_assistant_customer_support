from typing import AsyncIterator, Protocol


class LLMProvider(Protocol):
    async def stream_response(self, user_input: str) -> AsyncIterator[str]:
        ...

    async def extract_json(self, prompt: str) -> str:
        ...
