"""Versioned acceptance of the bounded HeyGen duration-failure charge."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import jwt

from app.core.config import settings
from app.core.exceptions import AppError
from app.schemas.billing import AvatarDurationPolicyOffer

VERSION = "145s-no-refund-v1"
MAX_SECONDS = Decimal("145")
ERROR_CODE = "HEYGEN_AUDIO_DURATION_EXCEEDED"
FAILURE_MESSAGE = "生成失败（超145秒，费用不退）"
NOTICE = (
    "数字人视频最长支持145秒。若实际配音时长超过145秒导致生成失败，"
    "本次配音费和视频生成费均不退还。请在提交前确认文案与语速。"
)
_AUDIENCE = "huading-avatar-duration-policy-v1"
_TTL = timedelta(minutes=10)


def _key():
    return hmac.new(settings.jwt_secret_key.encode(), _AUDIENCE.encode(), hashlib.sha256).digest()


def _invalid():
    return AppError(
        "时长政策凭证无效或已过期，请重新确认报价。",
        code="AVATAR_DURATION_POLICY_INVALID",
        status_code=422,
    )


def _binding(
    *, tenant_id, user_id, request_hash, model, pricing_contract, accepted_credits, quote_token
):
    if type(accepted_credits) is not int or accepted_credits < 0:
        raise _invalid()
    if model not in {"avatar_iv", "lipsync_precision"}:
        raise _invalid()
    return dict(
        tenant_id=tenant_id,
        user_id=user_id,
        request_hash=request_hash,
        model=model,
        pricing_contract=pricing_contract,
        accepted_credits=accepted_credits,
        quote_hash=hashlib.sha256((quote_token or "").encode()).hexdigest(),
        policy_version=VERSION,
    )


def issue_policy(*, now=None, **binding) -> AvatarDurationPolicyOffer:
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    claims = _binding(**binding)
    token = jwt.encode(
        {**claims, "aud": _AUDIENCE, "iat": now, "exp": now + _TTL}, _key(), algorithm="HS256"
    )
    return AvatarDurationPolicyOffer(
        version=VERSION,
        max_seconds=145,
        notice=NOTICE,
        accepted_credits=claims["accepted_credits"],
        token=token,
        expires_at=now + _TTL,
    )


def _decode(token, *, now):
    if not isinstance(token, str) or len(token) > 4096:
        raise _invalid()
    try:
        claims = jwt.decode(
            token,
            _key(),
            algorithms=["HS256"],
            audience=_AUDIENCE,
            options={
                "verify_exp": False,
                "verify_iat": False,
                "require": ["exp", "iat", "aud", "policy_version"],
            },
        )
        if type(claims["iat"]) is not int or type(claims["exp"]) is not int:
            raise _invalid()
        if not claims["iat"] <= now.timestamp() < claims["exp"]:
            raise _invalid()
        if claims["exp"] - claims["iat"] != int(_TTL.total_seconds()):
            raise _invalid()
        if claims["policy_version"] != VERSION:
            raise _invalid()
        return claims
    except (jwt.PyJWTError, TypeError, ValueError, KeyError) as exc:
        raise _invalid() from exc


def verify_acceptance(*, version, token, now=None, **binding):
    if version != VERSION or not token:
        raise AppError(
            "请先确认数字人145秒时长及超限失败不退款政策。",
            code="AVATAR_DURATION_POLICY_REQUIRED",
            status_code=422,
        )
    now = now or datetime.now(UTC)
    expected = _binding(**binding)
    claims = _decode(token, now=now)
    if any(type(claims.get(k)) is not type(v) or claims.get(k) != v for k, v in expected.items()):
        raise _invalid()
    return {**expected, "token": token, "accepted_at": now.isoformat()}


def validate_receipt(receipt, *, tenant_id, user_id):
    """Recheck persisted acceptance without treating worker delay as quote expiry."""
    if not isinstance(receipt, dict):
        raise _invalid()
    try:
        accepted_at = datetime.fromisoformat(receipt["accepted_at"])
        claims = _decode(receipt["token"], now=accepted_at)
        keys = (
            "policy_version",
            "tenant_id",
            "user_id",
            "request_hash",
            "model",
            "pricing_contract",
            "accepted_credits",
            "quote_hash",
        )
        if any(
            type(receipt.get(k)) is not type(claims.get(k)) or receipt.get(k) != claims.get(k)
            for k in keys
        ):
            raise _invalid()
        if claims["tenant_id"] != tenant_id or claims["user_id"] != user_id:
            raise _invalid()
        amount = claims["accepted_credits"]
        if type(amount) is not int or amount < 0:
            raise _invalid()
        return amount
    except (TypeError, KeyError, ValueError) as exc:
        raise _invalid() from exc


def measured_duration(value) -> Decimal:
    try:
        if value is None or isinstance(value, bool):
            raise ValueError("missing meter")
        duration = Decimal(str(value))
        if not duration.is_finite() or duration <= 0:
            raise ValueError("invalid meter")
        return duration
    except (InvalidOperation, ValueError) as exc:
        raise AppError(
            "可信音频时长缺失。", code="HEYGEN_AUDIO_METER_INVALID", status_code=422
        ) from exc
