def base_mime(raw: str | None) -> str:
    return (raw or "").split(";", 1)[0].strip().lower()


def normalize_tenant_slug(raw: str | None) -> str:
    return (raw or "").strip().lower()
