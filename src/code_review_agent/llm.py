"""Model selection, provider-agnostic via LangChain's ``init_chat_model``.

Pick any provider with a "provider:model" string, e.g.
    anthropic:claude-opus-5    openai:<model>    google_genai:<model>    ollama:llama3.2
The model can be set per run through LangGraph's config: {"configurable": {"model": "..."}}.
"""

import os
from functools import lru_cache
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

DEFAULT_MODEL = "anthropic:claude-opus-5"

# Providers whose chat model accepts a `max_tokens` argument. Structured findings can be long, and
# some providers default to a low output cap that would cut the JSON off mid-way.
_MAX_TOKENS_PROVIDERS = {"anthropic", "openai", "azure_openai", "groq", "mistralai"}


def default_model() -> str:
    return os.environ.get("REVIEW_MODEL", DEFAULT_MODEL)


@lru_cache(maxsize=8)
def _build(model: str) -> BaseChatModel:
    provider = model.split(":", 1)[0] if ":" in model else ""
    kwargs: dict[str, Any] = {}
    if provider in _MAX_TOKENS_PROVIDERS:
        kwargs["max_tokens"] = 16_000
    return init_chat_model(model, **kwargs)


def get_llm(config: RunnableConfig | None) -> BaseChatModel:
    """The chat model for this run. Tests inject a fake through ``configurable.llm``."""
    configurable = (config or {}).get("configurable", {})
    if configurable.get("llm") is not None:
        return configurable["llm"]
    return _build(configurable.get("model") or default_model())
