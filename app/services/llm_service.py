import json
import re
from datetime import datetime
from typing import AsyncIterator

from ollama import AsyncClient

from app.core.config import OLLAMA_MODEL, OLLAMA_SYSTEM_PROMPT, OLLAMA_URL

PROMPT_TEMPLATE = """
Extract structured information from the user input.

Today is {today}.
Available departments: {departments}.

Return ONLY valid JSON with these exact fields:
- intent: one of "book_appointment", "check_availability", "unknown"
- department: one of the available departments above, or null
- date: normalized calendar date in YYYY-MM-DD format, or null
- time: normalized time in HH:MM:SS 24-hour format, or null
- name: user's name if mentioned, else null

Rules:
- Resolve natural language references like "today", "tomorrow", "next monday" using the provided today date.
- If only a date is mentioned, set time to null.
- If only a time is mentioned, set date to null.
- If uncertain, set the field to null.
- Do not include extra keys.

User input: "{input}"
"""

_OLLAMA_CLIENT: AsyncClient | None = None
ALLOWED_INTENTS = {"book_appointment", "check_availability", "unknown"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$")


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
        content = chunk.message.content if hasattr(chunk, "message") else chunk.get("message", {}).get("content", "")
        if content:
            yield content


async def extract_intent(user_input: str, available_departments: list[str] | None = None):
    normalized_departments = _normalize_departments(available_departments)
    department_text = ", ".join(normalized_departments) if normalized_departments else "none"
    prompt = PROMPT_TEMPLATE.format(
        input=user_input,
        today=datetime.now().strftime("%Y-%m-%d"),
        departments=department_text,
    )

    try:
        response = await _get_ollama_client().chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            format="json",
        )
        raw_content = response.message.content if hasattr(response, "message") else response["message"]["content"]
        print("LLM raw response:", raw_content)
        parsed = _safe_json_load(raw_content)
        return _normalize_intent_payload(parsed, normalized_departments)
    except Exception as error:
        print(f"Error parsing LLM response: {error}")
        return _default_payload()


def _safe_json_load(raw_content: str) -> dict:
    try:
        loaded = json.loads(raw_content)
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        pass

    # Some model responses include extra text around JSON. Recover the first object.
    start = raw_content.find("{")
    end = raw_content.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = raw_content[start : end + 1]
        try:
            loaded = json.loads(snippet)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass

    return _default_payload()


def _normalize_nullable_text(value) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    normalized = value.strip().lower()
    if not normalized or normalized in {"null", "none", "unknown", "n/a", "na"}:
        return None

    return value.strip()


def _normalize_departments(departments: list[str] | None) -> list[str]:
    if not departments:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in departments:
        value = _normalize_nullable_text(item)
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _normalize_intent_payload(payload: dict, allowed_departments: list[str]) -> dict:
    intent_value = _normalize_nullable_text(payload.get("intent"))
    intent_candidate = intent_value.lower() if intent_value else None
    intent = intent_candidate if intent_candidate in ALLOWED_INTENTS else "unknown"

    department_value = _normalize_nullable_text(payload.get("department"))
    department = department_value.lower() if department_value else None
    if department and department not in allowed_departments:
        department = None

    return {
        "intent": intent,
        "department": department,
        "date": _normalize_date(payload.get("date")),
        "time": _normalize_time(payload.get("time")),
        "name": _normalize_nullable_text(payload.get("name")),
    }


def _normalize_date(value) -> str | None:
    text_value = _normalize_nullable_text(value)
    if not text_value or not DATE_RE.match(text_value):
        return None

    try:
        datetime.strptime(text_value, "%Y-%m-%d")
        return text_value
    except ValueError:
        return None


def _normalize_time(value) -> str | None:
    text_value = _normalize_nullable_text(value)
    if not text_value or not TIME_RE.match(text_value):
        return None
    return text_value


def _default_payload() -> dict:
    return {
        "intent": "unknown",
        "department": None,
        "date": None,
        "time": None,
        "name": None,
    }
