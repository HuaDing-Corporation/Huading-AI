from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest

from app.core import security
from app.core.config import Settings

_JWT_SECRET = "session-token-test-secret-at-least-32-characters"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_access_token_default_lifetime_is_24_hours(monkeypatch) -> None:
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
    configured = Settings(_env_file=None, jwt_secret_key=_JWT_SECRET)
    monkeypatch.setattr(security, "settings", configured)
    issued_after = datetime.now(UTC)

    token = security.create_access_token(
        user_id="session-user",
        tenant_id="session-tenant",
        role="admin",
    )
    payload = jwt.decode(
        token,
        _JWT_SECRET,
        algorithms=[configured.jwt_algorithm],
    )
    expires_at = datetime.fromtimestamp(payload["exp"], UTC)

    assert configured.access_token_expire_minutes == 24 * 60
    assert timedelta(hours=24) - timedelta(seconds=1) <= expires_at - issued_after
    assert expires_at - issued_after <= timedelta(hours=24) + timedelta(seconds=1)


@pytest.mark.parametrize(
    "relative_path",
    [
        "backend/.env.example",
        "infra/.env.example",
        "infra/.env.prod.example",
    ],
)
def test_access_token_lifetime_is_explicit_in_env_examples(relative_path: str) -> None:
    lines = {
        line.strip()
        for line in (_REPO_ROOT / relative_path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "ACCESS_TOKEN_EXPIRE_MINUTES=1440" in lines
