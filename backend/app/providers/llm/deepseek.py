from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI


class DeepSeekProvider:
    def __init__(self, *, api_key: str, base_url: str, model: str) -> None:
        if not api_key or not base_url or not model:
            raise ValueError("DeepSeek api_key, base_url, and model are required.")
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate_text(self, payload: dict[str, Any]) -> dict[str, str]:
        topic = str(payload.get("topic") or "").strip()
        if not topic:
            raise ValueError("topic is required.")
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "Write concise spoken-video scripts for ecommerce presenters.",
                },
                {
                    "role": "user",
                    "content": f"Create a short digital-human spoken script about: {topic}",
                },
            ],
            temperature=0.7,
        )
        text = response.choices[0].message.content or ""
        return {"text": text.strip()}
