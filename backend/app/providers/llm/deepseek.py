from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.db.models import ProviderConfig
from app.providers.base import register_provider


class DeepSeekProvider:
    def __init__(self, *, api_key: str, base_url: str, model: str) -> None:
        if not api_key or not base_url or not model:
            raise ValueError("DeepSeek api_key, base_url, and model are required.")
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        topic = str(payload.get("topic") or "").strip()
        if not topic:
            raise ValueError("topic is required.")
        system_prompt = str(
            payload.get("system_prompt")
            or "Write concise spoken-video scripts for ecommerce presenters."
        )
        user_prompt = str(
            payload.get("user_prompt")
            or f"Create a short digital-human spoken script about: {topic}"
        )
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            temperature=0.7,
        )
        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        return {
            "text": text.strip(),
            "provider": "deepseek",
            "model": self.model,
            "usage": {
                "prompt_tokens": _usage_int(usage, "prompt_tokens"),
                "completion_tokens": _usage_int(usage, "completion_tokens"),
                "total_tokens": _usage_int(usage, "total_tokens"),
            },
        }


def _usage_int(usage: Any, key: str) -> int:
    if usage is None:
        return 0
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, 0)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _deepseek_factory(config: ProviderConfig) -> DeepSeekProvider:
    values = config.config or {}
    return DeepSeekProvider(
        api_key=str(values.get("api_key") or settings.engine_llm_api_key),
        base_url=str(values.get("base_url") or settings.engine_llm_base_url),
        model=str(values.get("model") or settings.engine_llm_model),
    )


register_provider("llm", "deepseek", _deepseek_factory)
