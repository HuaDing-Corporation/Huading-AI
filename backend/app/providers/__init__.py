"""Provider abstraction package.

Import built-in adapters for registry side effects.
"""

from app.providers.avatar import omnihuman as _omnihuman  # noqa: F401
from app.providers.image import openai as _openai_image  # noqa: F401
from app.providers.llm import deepseek as _deepseek  # noqa: F401
from app.providers.tts import doubao_seed_tts_provider as _doubao_seed_tts_provider  # noqa: F401
from app.providers.tts import edge_tts_provider as _edge_tts_provider  # noqa: F401
from app.providers.voice_clone import doubao as _doubao_voice_clone  # noqa: F401
