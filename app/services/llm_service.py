import json
from typing import AsyncIterator

from ollama import AsyncClient

from app.core.config import OLLAMA_MODEL, OLLAMA_SYSTEM_PROMPT, OLLAMA_URL

PROMPT_TEMPLATE = """
Extract structured information from user input.

Return ONLY JSON.

Fields:
- intent: (book_appointment | check_availability | unknown)
- department: (cardiology, dermatology, general, null)
- date: (today, tomorrow, monday, tuesday, etc. or null)
- name: (if mentioned, else null)

If not mentioned, return null.

User input: "{input}"
"""

_OLLAMA_CLIENT: AsyncClient | None = None


def _get_ollama_client() -> AsyncClient:
    global _OLLAMA_CLIENT
    if _OLLAMA_CLIENT is None:
        _OLLAMA_CLIENT = AsyncClient(host=OLLAMA_URL) if OLLAMA_URL else AsyncClient()
    return _OLLAMA_CLIENT


async def stream_gwen_response(user_input: str) -> AsyncIterator[str]:
    stream = await _get_ollama_client().chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
            {"role": "user", "content": user_input},
        ],
        stream=True,
    )

    async for chunk in stream:
        content = chunk.get("message", {}).get("content", "")
        if content:
            yield content


async def extract_intent(user_input: str):
    prompt = PROMPT_TEMPLATE.format(input=user_input)

    try:
        response = await _get_ollama_client().chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            format="json",
        )
        raw_content = response["message"]["content"]
        print("LLM raw response:", raw_content)
        return json.loads(raw_content)
    except Exception as error:
        print(f"Error parsing LLM response: {error}")
        return {
            "intent": "unknown",
            "department": "unknown",
            "date": None,
            "name": None,
        }
