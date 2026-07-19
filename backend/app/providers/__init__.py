"""Provider abstraction package.

Import built-in adapters for registry side effects.
"""

from app.providers.avatar import omnihuman as _omnihuman  # noqa: F401
from app.providers.chat import apimart_gpt56 as _apimart_gpt56_chat  # noqa: F401
from app.providers.image import apimart as _apimart_image  # noqa: F401
from app.providers.image import openai as _openai_image  # noqa: F401
from app.providers.llm import deepseek as _deepseek  # noqa: F401
from app.providers.reverse_prompt import (
    apimart_gemini as _apimart_gemini_reverse_prompt,  # noqa: F401
)
from app.providers.scene_prompt import apimart_luna as _apimart_luna_scene_prompt  # noqa: F401
from app.providers.tts import doubao_seed_tts_provider as _doubao_seed_tts_provider  # noqa: F401
from app.providers.tts import edge_tts_provider as _edge_tts_provider  # noqa: F401
from app.providers.video import apimart as _apimart_video  # noqa: F401
from app.providers.video import seedance_mini as _seedance_mini  # noqa: F401
from app.providers.voice_clone import cosyvoice as _cosyvoice_voice_clone  # noqa: F401
from app.providers.voice_clone import doubao as _doubao_voice_clone  # noqa: F401
