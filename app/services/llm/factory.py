from app.core.config import LLM_PROVIDER
from app.services.llm.base import LLMProvider
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.ollama_provider import OllamaProvider

_PROVIDER: LLMProvider | None = None
_PROVIDER_NAME_OVERRIDE: str | None = None


def set_llm_provider(provider_name: str) -> None:
    global _PROVIDER_NAME_OVERRIDE, _PROVIDER
    normalized_name = provider_name.strip().lower()
    if normalized_name not in {"groq", "ollama"}:
        raise RuntimeError(
            f"Unsupported provider={provider_name!r}. Supported values: 'groq', 'ollama'"
        )

    _PROVIDER_NAME_OVERRIDE = normalized_name
    _PROVIDER = None


def get_llm_provider() -> LLMProvider:
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER

    provider_name = _PROVIDER_NAME_OVERRIDE or LLM_PROVIDER.strip().lower()

    if provider_name == "groq":
        _PROVIDER = GroqProvider()
        return _PROVIDER

    if provider_name == "ollama":
        _PROVIDER = OllamaProvider()
        return _PROVIDER

    raise RuntimeError(
        f"Unsupported LLM_PROVIDER={LLM_PROVIDER!r}. Supported values: 'groq', 'ollama'"
    )
