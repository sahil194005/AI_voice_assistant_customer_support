from collections.abc import AsyncIterator

from groq import AsyncGroq

from app.core.config import GROQ_API_KEY, GROQ_MODEL, LLM_SYSTEM_PROMPT


class GroqProvider:
    def __init__(self) -> None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is required when LLM_PROVIDER=groq")
        if not GROQ_MODEL:
            raise RuntimeError("GROQ_MODEL is required when LLM_PROVIDER=groq")

        self._client = AsyncGroq(api_key=GROQ_API_KEY)

    async def stream_response(self, user_input: str) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ],
            stream=True,
        )

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = delta.content
            if content:
                yield content

    async def extract_json(self, prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        if not response.choices:
            raise RuntimeError("Groq returned no choices for structured extraction")

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Groq returned empty content for structured extraction")

        return content
