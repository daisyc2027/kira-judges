import os
from dataclasses import dataclass


def _flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class KiraConfig:
    ai_provider: str
    ai_api_key: str
    ai_base_url: str
    chat_model: str
    sleep_model: str
    diet_model: str
    classifier_model: str
    embedding_model: str
    prompt_version: str
    rag_version: str
    enable_rag: bool
    enable_session_cache: bool
    enable_safety_router: bool
    debug_chat: bool
    require_auth: bool

    @classmethod
    def from_env(cls):
        provider = os.getenv("KIRA_AI_PROVIDER", "gemini").strip().lower()
        inferred_chat_model = ""
        if provider.startswith("gemini-"):
            inferred_chat_model = provider
            provider = "gemini"
        elif provider.startswith("gpt-"):
            inferred_chat_model = provider
            provider = "openai"
        elif provider.startswith("deepseek-"):
            inferred_chat_model = provider
            provider = "deepseek"

        default_base_urls = {
            "deepseek": "https://api.deepseek.com",
            "openai": "",
            "openai_compatible": os.getenv("KIRA_AI_BASE_URL", ""),
            "gemini": "https://generativelanguage.googleapis.com/v1beta",
        }
        default_models = {
            "deepseek": "deepseek-chat",
            "openai": "gpt-4o-mini",
            "openai_compatible": "gpt-4o-mini",
            "gemini": "gemini-3.5-flash-lite",
        }
        provider_key_env = {
            "deepseek": "DEEPSEEK_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openai_compatible": "KIRA_AI_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }
        provider_specific_key = os.getenv(provider_key_env.get(provider, ""), "")
        api_key = provider_specific_key or os.getenv("KIRA_AI_API_KEY") or ""
        chat_model = os.getenv("KIRA_CHAT_MODEL") or inferred_chat_model or default_models.get(provider, "gpt-4o-mini")
        return cls(
            ai_provider=provider,
            ai_api_key=api_key,
            ai_base_url=os.getenv("KIRA_AI_BASE_URL", default_base_urls.get(provider, "")),
            chat_model=chat_model,
            sleep_model=os.getenv("KIRA_SLEEP_MODEL", chat_model),
            diet_model=os.getenv("KIRA_DIET_MODEL", chat_model),
            classifier_model=os.getenv("KIRA_CLASSIFIER_MODEL", chat_model),
            embedding_model=os.getenv("KIRA_EMBEDDING_MODEL", "hashing-v1"),
            prompt_version=os.getenv("KIRA_PROMPT_VERSION", "v1"),
            rag_version=os.getenv("KIRA_RAG_VERSION", "v1"),
            enable_rag=_flag("ENABLE_RAG", True),
            enable_session_cache=_flag("ENABLE_SESSION_CACHE", True),
            enable_safety_router=_flag("ENABLE_SAFETY_ROUTER", True),
            debug_chat=_flag("KIRA_DEBUG_CHAT", False),
            require_auth=_flag("KIRA_REQUIRE_AUTH", True),
        )
