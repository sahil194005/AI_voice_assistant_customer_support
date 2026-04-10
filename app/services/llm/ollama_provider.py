from collections.abc import AsyncIterator

from ollama import AsyncClient

from app.core.config import OLLAMA_MODEL, OLLAMA_SYSTEM_PROMPT, OLLAMA_URL


class OllamaProvider:
    def __init__(self) -> None:
        if not OLLAMA_MODEL:
            raise RuntimeError("OLLAMA_MODEL is required when LLM_PROVIDER=ollama")

        self._client = AsyncClient(host=OLLAMA_URL) if OLLAMA_URL else AsyncClient()

    async def stream_response(self, user_input: str) -> AsyncIterator[str]:
        stream = await self._client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ],
            stream=True,
        )

        async for chunk in stream:
            content = chunk.message.content
            if content:
                yield content

    async def extract_json(self, prompt: str) -> str:
        response = await self._client.chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            format="json",
        )
        return response.message.content
