def base_mime(raw: str | None) -> str:
    return (raw or "").split(";", 1)[0].strip().lower()
