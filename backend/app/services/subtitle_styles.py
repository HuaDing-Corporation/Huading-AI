from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SUBTITLE_TEMPLATES: tuple[dict[str, object], ...] = (
    {
        "id": "classic",
        "name": "经典白",
        "font_family": "Noto Sans SC",
        "font_size": 48,
        "color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 2,
        "background": None,
        "position": "bottom",
    },
    {
        "id": "bold_yellow",
        "name": "醒目黄",
        "font_family": "Noto Sans SC",
        "font_size": 56,
        "color": "#FFE600",
        "stroke_color": "#000000",
        "stroke_width": 3,
        "background": None,
        "position": "bottom",
    },
    {
        "id": "boxed",
        "name": "底条黑",
        "font_family": "Noto Sans SC",
        "font_size": 46,
        "color": "#FFFFFF",
        "stroke_color": None,
        "stroke_width": 0,
        "background": "#000000B3",
        "position": "bottom",
    },
    {
        "id": "minimal",
        "name": "极简灰",
        "font_family": "Noto Sans SC",
        "font_size": 40,
        "color": "#EAEAEA",
        "stroke_color": None,
        "stroke_width": 0,
        "background": None,
        "position": "bottom",
    },
    {
        "id": "top_news",
        "name": "顶部条",
        "font_family": "Noto Sans SC",
        "font_size": 44,
        "color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 2,
        "background": "#0A0A0AB3",
        "position": "top",
    },
)

SUBTITLE_TEMPLATE_IDS = frozenset(str(item["id"]) for item in SUBTITLE_TEMPLATES)
_TEMPLATES_BY_ID = {str(item["id"]): item for item in SUBTITLE_TEMPLATES}


def subtitle_templates() -> list[dict[str, object]]:
    return [dict(item) for item in SUBTITLE_TEMPLATES]


def clamp_subtitle_font_size(value: int) -> int:
    return max(16, min(96, int(value)))


def resolve_subtitle_style(style: Mapping[str, Any] | None) -> dict[str, object] | None:
    if not style:
        return None
    template_id = str(style.get("template_id") or "")
    template = _TEMPLATES_BY_ID.get(template_id)
    if template is None:
        raise ValueError(f"Unknown subtitle template: {template_id}")

    resolved = dict(template)
    for key in ("font_family", "color", "position"):
        value = style.get(key)
        if value is not None:
            resolved[key] = value
    if style.get("font_size") is not None:
        resolved["font_size"] = clamp_subtitle_font_size(int(style["font_size"]))
    return resolved
