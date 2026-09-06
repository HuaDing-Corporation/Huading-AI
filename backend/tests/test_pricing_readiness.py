from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    Base,
    BillingOperation,
    BrandVoice,
    BrandVoiceOrder,
    BrandVoiceProviderId,
    CreditRate,
    CreditRefundGrant,
    Plan,
    ProviderConfig,
    Subscription,
    Tenant,
    UsageRecord,
    User,
)

_RELEASE_MIGRATION_REVISION = "20260829_0038"
_LEGACY_MIGRATION_REVISION = "20260724_0031"


@pytest.fixture(autouse=True)
def _stamp_release_revision_for_model_schema(request):
    """Base.metadata schemas in this module model the fully migrated release."""
    if "db_session" not in request.fixturenames:
        yield
        return
    session = request.getfixturevalue("db_session")
    session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32))"))
    session.execute(text("DELETE FROM alembic_version"))
    session.execute(
        text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
        {"revision": _RELEASE_MIGRATION_REVISION},
    )
    session.commit()
    yield


@pytest.fixture
def legacy_pricing_session():
    """Small real 0031-shaped database with no 0036-0038 tables or columns."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        for statement in (
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)",
            """
            CREATE TABLE credit_rates (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36),
                capability VARCHAR(64) NOT NULL,
                unit VARCHAR(32) NOT NULL,
                credits_per_unit NUMERIC(12, 4) NOT NULL,
                is_active BOOLEAN NOT NULL,
                effective_at DATETIME NOT NULL,
                CONSTRAINT ck_credit_rates_capability CHECK (capability <> '')
            )
            """,
            """
            CREATE TABLE reasoning_wallets (
                tenant_id VARCHAR(36) PRIMARY KEY,
                available_credits INTEGER NOT NULL DEFAULT 0,
                CONSTRAINT ck_reasoning_wallets_available_nonnegative
                    CHECK (available_credits >= 0)
            )
            """,
            "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY)",
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)",
            "CREATE TABLE chat_messages (id VARCHAR(36) PRIMARY KEY)",
            "CREATE TABLE assets (id VARCHAR(36) PRIMARY KEY)",
            """
            CREATE TABLE admin_audit_logs (
                id VARCHAR(36) PRIMARY KEY,
                action VARCHAR(64) NOT NULL,
                CONSTRAINT ck_admin_audit_logs_action
                    CHECK (action IN ('credits_adjust', 'plan_change', 'status_change',
                                      'voice_slot_assign', 'task_retry'))
            )
            """,
            """
            CREATE TABLE usage_records (
                id VARCHAR(36) PRIMARY KEY,
                subscription_id VARCHAR(36),
                capability VARCHAR(64) NOT NULL DEFAULT 'publish',
                unit VARCHAR(32) NOT NULL DEFAULT 'call',
                quantity NUMERIC(12, 3) NOT NULL,
                credits NUMERIC(18, 6) NOT NULL,
                status VARCHAR(32),
                CONSTRAINT ck_usage_records_capability CHECK (capability <> ''),
                CONSTRAINT ck_usage_records_unit CHECK (unit <> '')
            )
            """,
            """
            CREATE TABLE subscriptions (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL,
                status VARCHAR(32) NOT NULL,
                quota_credits_total INTEGER NOT NULL,
                quota_credits_used INTEGER NOT NULL,
                quota_credits_reserved INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE provider_configs (
                id VARCHAR(36) PRIMARY KEY,
                config TEXT
            )
            """,
            """
            CREATE TABLE brand_voices (
                id VARCHAR(36) PRIMARY KEY,
                provider VARCHAR(64) NOT NULL,
                speaker_id VARCHAR(160)
            )
            """,
        ):
            connection.execute(text(statement))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": _LEGACY_MIGRATION_REVISION},
        )
        connection.execute(
            text(
                """
                INSERT INTO credit_rates
                    (id, tenant_id, capability, unit, credits_per_unit, is_active, effective_at)
                VALUES
                    ('legacy-video', NULL, 'video', 'second', 80, 1, CURRENT_TIMESTAMP),
                    ('legacy-video-gen', NULL, 'video_gen', 'second', 80, 1, CURRENT_TIMESTAMP),
                    ('legacy-image', NULL, 'image', 'image', 10, 1, CURRENT_TIMESTAMP),
                    ('legacy-reverse', NULL, 'reverse_prompt', 'call', 30, 1, CURRENT_TIMESTAMP),
                    ('legacy-avatar', NULL, 'avatar', 'second', 150, 1, CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO subscriptions
                    (id, tenant_id, status, quota_credits_total,
                     quota_credits_used, quota_credits_reserved)
                VALUES ('legacy-subscription', 'tenant-a', 'active', 100, 10, 0)
                """
            )
        )
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def empty_bootstrap_session():
    """A dedicated database with no application or Alembic tables."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _legacy_snapshot(session: Session) -> dict[str, list[tuple]]:
    return {
        table_name: [tuple(row) for row in session.execute(text(f"SELECT * FROM {table_name}"))]
        for table_name in (
            "alembic_version",
            "credit_rates",
            "usage_records",
            "subscriptions",
            "provider_configs",
            "brand_voices",
        )
    }


def _legacy_preflight_report(session: Session, monkeypatch):
    from scripts.ops import pricing_closure_readiness as readiness

    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["signed-official-voice"],
    )
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )
    return readiness.pricing_closure_preflight(session, production_mode=True)


def _install_minimal_0036_billing_schema(session: Session) -> None:
    _install_0034_cooldown_table(session, relational_contract=True)
    session.execute(
        text(
            """
            CREATE TABLE billing_operations (
                id VARCHAR(36) PRIMARY KEY,
                requested_credits NUMERIC NOT NULL,
                settled_credits NUMERIC NOT NULL,
                released_credits NUMERIC NOT NULL
            )
            """
        )
    )
    for column_sql in (
        "billing_operation_id VARCHAR(36)",
        "billing_item_index INTEGER",
        "billing_pricing_line_index INTEGER",
        "provider_usage JSON",
    ):
        session.execute(text(f"ALTER TABLE usage_records ADD COLUMN {column_sql}"))


def _install_complete_0036_billing_schema(session: Session) -> None:
    _install_0034_cooldown_table(session, relational_contract=True)
    session.execute(
        text(
            """
            CREATE TABLE billing_operations (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                operation VARCHAR(64) NOT NULL,
                idempotency_key VARCHAR(128) NOT NULL,
                request_hash VARCHAR(64) NOT NULL,
                quote_hash VARCHAR(64) NOT NULL,
                pricing_snapshot JSON NOT NULL,
                requested_credits NUMERIC NOT NULL,
                settled_credits NUMERIC NOT NULL,
                released_credits NUMERIC NOT NULL,
                status VARCHAR(32) NOT NULL,
                completion_kind VARCHAR(32),
                completed_at DATETIME,
                result_type VARCHAR(64),
                result_id VARCHAR(128),
                result_payload JSON,
                error_code VARCHAR(64),
                error_http_status INTEGER,
                error_payload JSON,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
    )
    for column_sql in (
        "billing_operation_id VARCHAR(36)",
        "billing_item_index INTEGER",
        "billing_pricing_line_index INTEGER",
        "provider_usage JSON",
    ):
        session.execute(text(f"ALTER TABLE usage_records ADD COLUMN {column_sql}"))


def _install_0034_cooldown_table(
    session: Session,
    *,
    relational_contract: bool,
) -> None:
    relational_sql = (
        """
                , CONSTRAINT uq_aibrain_user_cooldowns_user_id UNIQUE (user_id)
                , FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
                , FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                , FOREIGN KEY (source_message_id) REFERENCES chat_messages(id) ON DELETE SET NULL
        """
        if relational_contract
        else ""
    )
    session.execute(
        text(
            f"""
            CREATE TABLE aibrain_user_cooldowns (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                reason VARCHAR(64) NOT NULL,
                source_message_id VARCHAR(36),
                expires_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
                {relational_sql}
            )
            """
        )
    )
    if relational_contract:
        session.execute(
            text(
                "CREATE INDEX ix_aibrain_user_cooldowns_tenant_id "
                "ON aibrain_user_cooldowns (tenant_id)"
            )
        )
        session.execute(
            text(
                "CREATE INDEX ix_aibrain_user_cooldowns_tenant_expires "
                "ON aibrain_user_cooldowns (tenant_id, expires_at)"
            )
        )


def _session_scope(session):
    @contextmanager
    def scope():
        yield session

    return scope


def _reserve_zero_price_operation(db_session) -> BillingOperation:
    from uuid import uuid4

    from app.services.billing_operations import UsageAllocation, create_reserved_operation
    from app.services.billing_quotes import VerifiedQuote
    from app.services.pricing import (
        PricingLine,
        PricingSnapshot,
        RateScope,
        RateSource,
        ResolvedRate,
    )

    rate = ResolvedRate(
        unit_credits=Decimal("0"),
        source=RateSource.FIXED_POLICY,
        rate_id=None,
        effective_at=None,
        policy_key="cosyvoice_brand_voice_create",
        policy_version=1,
    )
    line = PricingLine(
        operation="cosyvoice_brand_voice_create",
        capability="voice_clone",
        unit="call",
        quantity=Decimal("1"),
        unit_credits=Decimal("0"),
        subtotal_credits=Decimal("0"),
        rate_scope=RateScope.PLATFORM_FIXED,
        rate=rate,
        label="CosyVoice clone",
    )
    quote = VerifiedQuote(
        snapshot=PricingSnapshot(
            operation="cosyvoice_brand_voice_create",
            pricing_shape="simple",
            pricing_lines=(line,),
            disclosures=(),
            subtotal_credits=Decimal("0"),
            payable_credits=0,
        ),
        quote_hash="d" * 64,
        pricing_payload_hash="e" * 64,
    )
    operation = create_reserved_operation(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        operation="cosyvoice_brand_voice_create",
        idempotency_key=uuid4(),
        request_hash="f" * 64,
        verified_quote=quote,
        usage_allocations=(
            UsageAllocation(
                item_index=0,
                pricing_line_index=0,
                quantity=Decimal("1"),
                credits=Decimal("0"),
                provider="cosyvoice",
                model=None,
                video_task_id=None,
            ),
        ),
    )
    db_session.commit()
    return operation


def _minimal_subprocess_env() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP")
        if key in os.environ
    }


def _valid_wrapper_payload(mode: str) -> tuple[int, dict[str, object]]:
    generated_at = "2026-08-29T12:00:00+00:00"
    if mode == "bootstrap-empty-preflight":
        return 0, {
            "ready": True,
            "production_mode": True,
            "migration_revision": None,
            "target_migration_revision": _RELEASE_MIGRATION_REVISION,
            "generated_at": generated_at,
            "blockers": [],
        }
    if mode == "preflight":
        return 2, {
            "ready": False,
            "production_mode": True,
            "migration_revision": _LEGACY_MIGRATION_REVISION,
            "target_migration_revision": _RELEASE_MIGRATION_REVISION,
            "generated_at": generated_at,
            "blockers": [
                {
                    "code": "PREFLIGHT_SCHEMA_MISSING",
                    "record_ids": ["billing_operations"],
                    "detail": "Required legacy preflight table is missing.",
                }
            ],
        }
    if mode == "audit":
        return 0, {
            "ready": True,
            "production_mode": True,
            "migration_revision": _RELEASE_MIGRATION_REVISION,
            "generated_at": generated_at,
            "platform_rates": [],
            "tenant_rates": [],
            "platform_rate_ids": [],
            "tenant_rate_ids": [],
            "inventory_unknown_count": 0,
            "provider_inventory_counts": {
                "official_configured_ids": 0,
                "legacy_configured_ids": 0,
                "provider_config_ids": 0,
                "brand_voice_ids": 0,
                "active_official_registry_ids": 0,
                "retired_official_registry_ids": 0,
                "active_customer_registry_ids": 0,
                "retired_customer_registry_ids": 0,
                "registry_blocker_ids": 0,
            },
            "legacy_slot_write_surfaces": [],
            "blockers": [],
        }
    assert mode == "register-official"
    return 2, {"error": "OFFICIAL_REGISTRATION_ABORTED"}


def _run_synthetic_readiness_wrapper(
    tmp_path: Path,
    *,
    mode: str,
    payload: dict[str, object],
    exit_code: int,
    extra_output: str = "",
) -> subprocess.CompletedProcess[str]:
    backend_root = tmp_path / "backend"
    ops_dir = backend_root / "scripts" / "ops"
    ops_dir.mkdir(parents=True)
    (backend_root / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (ops_dir / "__init__.py").write_text("", encoding="utf-8")
    source_wrapper = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "pricing_closure_readiness_cli.py"
    )
    wrapper = ops_dir / source_wrapper.name
    wrapper.write_text(source_wrapper.read_text(encoding="utf-8"), encoding="utf-8")
    (ops_dir / "pricing_closure_readiness.py").write_text(
        "import json\n\n"
        f"_PAYLOAD = {payload!r}\n"
        f"_EXIT_CODE = {exit_code!r}\n"
        f"_EXTRA_OUTPUT = {extra_output!r}\n\n"
        "def main(argv):\n"
        "    print(json.dumps(_PAYLOAD, sort_keys=True))\n"
        "    if _EXTRA_OUTPUT:\n"
        "        print(_EXTRA_OUTPUT)\n"
        "    return _EXIT_CODE\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-S", str(wrapper), mode],
        cwd=backend_root,
        env=_minimal_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_readiness_wrapper_help_uses_only_stdlib_and_names_safe_modes() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    script = backend_root / "scripts" / "ops" / "pricing_closure_readiness_cli.py"

    result = subprocess.run(
        [sys.executable, "-S", str(script), "--help"],
        cwd=backend_root,
        env=_minimal_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "preflight" in result.stdout
    assert "audit" in result.stdout
    assert "register-official" in result.stdout
    assert "bootstrap-empty-preflight" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("mode", "expected_exit_code"),
    [
        ("bootstrap-empty-preflight", 0),
        ("preflight", 2),
        ("audit", 0),
        ("register-official", 2),
    ],
)
def test_readiness_wrapper_direct_file_delegates_every_valid_mode(
    tmp_path: Path,
    mode: str,
    expected_exit_code: int,
) -> None:
    """Losing the backend import root would turn real delegation into a generic false green."""
    fixture_exit_code, fixture_payload = _valid_wrapper_payload(mode)
    assert fixture_exit_code == expected_exit_code

    result = _run_synthetic_readiness_wrapper(
        tmp_path,
        mode=mode,
        payload=fixture_payload,
        exit_code=fixture_exit_code,
    )

    assert result.returncode == expected_exit_code
    assert json.loads(result.stdout) == fixture_payload
    assert result.stderr == ""


def test_readiness_wrapper_rejects_extra_field_from_successful_inner_cli(
    tmp_path: Path,
) -> None:
    """A compromised inner command must not smuggle an unreviewed field through stdout."""
    _, payload = _valid_wrapper_payload("audit")
    private_marker = "postgresql://operator:secret@private.invalid/audit"
    payload["connection_debug"] = private_marker

    result = _run_synthetic_readiness_wrapper(
        tmp_path,
        mode="audit",
        payload=payload,
        exit_code=0,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {"error": "PRICING_READINESS_FAILED"}
    assert private_marker not in result.stdout
    assert private_marker not in result.stderr


def test_readiness_wrapper_rejects_ready_report_with_mismatched_exit(
    tmp_path: Path,
) -> None:
    """The wrapper must bind readiness truth to the documented process exit code."""
    _, payload = _valid_wrapper_payload("bootstrap-empty-preflight")

    result = _run_synthetic_readiness_wrapper(
        tmp_path,
        mode="bootstrap-empty-preflight",
        payload=payload,
        exit_code=2,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {"error": "PRICING_READINESS_FAILED"}
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("mode", "payload"),
    [
        (
            "preflight",
            {
                **_valid_wrapper_payload("preflight")[1],
                "migration_revision": "20990101_9999",
            },
        ),
        (
            "audit",
            {
                **_valid_wrapper_payload("audit")[1],
                "inventory_unknown_count": 1,
            },
        ),
        (
            "audit",
            {
                **_valid_wrapper_payload("audit")[1],
                "ready": False,
                "blockers": [
                    {
                        "code": "LEGACY_SLOT_WRITE_SURFACE",
                        "record_ids": ["admin_api:POST /tenants/{tenant_id}/voice-slots"],
                        "detail": (
                            "Legacy Doubao slot writing must be removed or explicitly retired."
                        ),
                    }
                ],
                "legacy_slot_write_surfaces": [
                    {
                        "surface": "admin_api:POST /tenants/{tenant_id}/voice-slots",
                        "state": "retired",
                    }
                ],
            },
        ),
    ],
)
def test_readiness_wrapper_rejects_internally_contradictory_evidence(
    tmp_path: Path,
    mode: str,
    payload: dict[str, object],
) -> None:
    """The redaction boundary must reject false-green and self-contradictory reports."""
    result = _run_synthetic_readiness_wrapper(
        tmp_path,
        mode=mode,
        payload=payload,
        exit_code=0 if payload["ready"] else 2,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {"error": "PRICING_READINESS_FAILED"}
    assert result.stderr == ""


def test_readiness_wrapper_rejects_arbitrary_inner_exit_code(tmp_path: Path) -> None:
    """Unexpected child exit codes must collapse to the registration-safe failure contract."""
    payload = {"registered_official_count": 1}

    result = _run_synthetic_readiness_wrapper(
        tmp_path,
        mode="register-official",
        payload=payload,
        exit_code=7,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {"error": "OFFICIAL_REGISTRATION_FAILED"}
    assert result.stderr == ""


def test_fresh_wrapper_audit_accepts_completed_brand_voice_result(tmp_path: Path) -> None:
    """The ops-only process must know the schema used by a real completed CosyVoice charge."""
    from app.schemas.brand_voices import BrandVoiceRead
    from app.services.billing_operations import complete_succeeded

    database_path = tmp_path / "brand-voice-audit.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": _RELEASE_MIGRATION_REVISION},
        )
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", slug="fresh-audit", name="Fresh audit"),
                Plan(
                    id="plan-a",
                    code="fresh-audit",
                    name="Fresh audit",
                    price_cents=0,
                    period="monthly",
                    quota_credits=100,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                User(
                    id="user-a",
                    tenant_id="tenant-a",
                    email="fresh-audit@example.com",
                    password_hash="hash",
                    role="creator",
                ),
                Subscription(
                    id="subscription-a",
                    tenant_id="tenant-a",
                    plan_id="plan-a",
                    status="active",
                    period_start=now - timedelta(days=1),
                    period_end=now + timedelta(days=30),
                    quota_credits_total=100,
                    quota_credits_used=0,
                    quota_credits_reserved=0,
                ),
            ]
        )
        session.commit()
        operation = _reserve_zero_price_operation(session)
        voice = BrandVoice(
            id="fresh-cosyvoice",
            tenant_id="tenant-a",
            owner_user_id="user-a",
            name="Fresh CosyVoice",
            provider="cosyvoice-voice-clone",
            speaker_id="fresh-speaker",
            status="ready",
            consent_confirmed=True,
            consent_confirmed_at=now,
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(voice)
        session.flush()
        complete_succeeded(
            session,
            operation_id=operation.id,
            actual_quantities={0: Decimal("1")},
            result_type="brand_voice",
            result_id=voice.id,
            result_payload=BrandVoiceRead(
                id=voice.id,
                name=voice.name,
                provider=voice.provider,
                status=voice.status,
                order_status=None,
                delivery_status="active",
                expires_at=None,
                created_at=voice.created_at,
            ),
        )
        session.commit()
        operation_id = operation.id
    engine.dispose()

    backend_root = Path(__file__).resolve().parents[1]
    wrapper = backend_root / "scripts" / "ops" / "pricing_closure_readiness_cli.py"
    child_env = _minimal_subprocess_env()
    child_env.update(
        {
            "DATABASE_URL": database_url,
            "ENVIRONMENT": "production",
            "ENGINE_DOUBAO_OFFICIAL_VOICE_IDS": "signed-official-voice",
            "JWT_SECRET_KEY": "fresh-audit-secret-fresh-audit-secret",
        }
    )

    result = subprocess.run(
        [sys.executable, str(wrapper), "audit"],
        cwd=backend_root,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["ready"] is False
    assert payload["migration_revision"] == _RELEASE_MIGRATION_REVISION
    assert (
        "BILLING_OPERATION_INVARIANT_FAILURE",
        (operation_id,),
    ) not in {
        (blocker["code"], tuple(blocker["record_ids"])) for blocker in payload["blockers"]
    }
    assert result.stderr == ""


def test_readiness_wrapper_redacts_invalid_argument_value() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    script = backend_root / "scripts" / "ops" / "pricing_closure_readiness_cli.py"
    private_marker = "postgresql://user:synthetic-secret@private.invalid/db"

    result = subprocess.run(
        [sys.executable, str(script), private_marker],
        cwd=backend_root,
        env=_minimal_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 64
    assert json.loads(result.stdout) == {"error": "PRICING_READINESS_ARGUMENT_ERROR"}
    assert result.stderr == ""
    assert private_marker not in result.stdout
    assert private_marker not in result.stderr


def test_register_official_wrapper_redacts_invalid_configuration_input() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    script = backend_root / "scripts" / "ops" / "pricing_closure_readiness_cli.py"
    private_marker = "synthetic-jwt-secret-marker"
    child_env = _minimal_subprocess_env()
    child_env["JWT_SECRET_KEY"] = private_marker

    result = subprocess.run(
        [sys.executable, str(script), "register-official"],
        cwd=backend_root,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {"error": "OFFICIAL_REGISTRATION_FAILED"}
    assert result.stderr == ""
    assert private_marker not in result.stdout
    assert private_marker not in result.stderr


def test_settings_validation_hides_invalid_input_value() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    private_marker = "synthetic-short-secret"
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, jwt_secret_key=private_marker)

    assert private_marker not in str(exc_info.value)


def test_preflight_cli_accepts_clean_0031_schema_without_writing(
    legacy_pricing_session,
    monkeypatch,
    capsys,
) -> None:
    """Removing the legacy-safe mode would strand the real 0031 production database."""
    from scripts.ops import pricing_closure_readiness as readiness

    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["signed-official-voice"],
    )
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )
    monkeypatch.setattr(
        readiness,
        "SessionLocal",
        _session_scope(legacy_pricing_session),
    )
    before = _legacy_snapshot(legacy_pricing_session)

    exit_code = readiness.main(["preflight"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ready"] is True
    assert payload["production_mode"] is True
    assert payload["migration_revision"] == _LEGACY_MIGRATION_REVISION
    assert payload["target_migration_revision"] == _RELEASE_MIGRATION_REVISION
    assert payload["blockers"] == []
    assert _legacy_snapshot(legacy_pricing_session) == before


@pytest.mark.parametrize("has_business_table", [False, True])
def test_empty_bootstrap_preflight_cli_accepts_only_a_truly_empty_database(
    empty_bootstrap_session,
    monkeypatch,
    capsys,
    has_business_table: bool,
) -> None:
    """First start must never reinterpret a partly initialized database as empty."""
    from scripts.ops import pricing_closure_readiness as readiness

    hidden_table_name = "sensitive_partial_business_table"
    if has_business_table:
        empty_bootstrap_session.execute(text(f"CREATE TABLE {hidden_table_name} (id INTEGER)"))
        empty_bootstrap_session.commit()
    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["signed-official-voice"],
    )
    monkeypatch.setattr(
        readiness,
        "SessionLocal",
        _session_scope(empty_bootstrap_session),
    )
    before_tables = tuple(
        empty_bootstrap_session.scalars(
            text("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        )
    )

    try:
        exit_code = readiness.main(["bootstrap-empty-preflight"])
    except SystemExit as exc:
        exit_code = int(exc.code)

    output = capsys.readouterr().out
    assert exit_code == (2 if has_business_table else 0)
    payload = json.loads(output)
    assert payload["ready"] is (not has_business_table)
    assert payload["migration_revision"] is None
    assert [blocker["code"] for blocker in payload["blockers"]] == (
        ["EMPTY_BOOTSTRAP_DATABASE_NOT_EMPTY"] if has_business_table else []
    )
    assert hidden_table_name not in output
    after_tables = tuple(
        empty_bootstrap_session.scalars(
            text("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        )
    )
    assert after_tables == before_tables


@pytest.mark.parametrize(
    "environment, configured_ids, expected_code",
    [
        ("local", ["signed-official-voice"], "PRODUCTION_ENVIRONMENT_REQUIRED"),
        ("production", [], "OFFICIAL_DOUBAO_CONFIG_MISSING"),
    ],
)
def test_empty_bootstrap_preflight_requires_production_and_signed_inventory(
    empty_bootstrap_session,
    monkeypatch,
    capsys,
    environment: str,
    configured_ids: list[str],
    expected_code: str,
) -> None:
    """An empty database is not authorization to start with unsafe release settings."""
    from scripts.ops import pricing_closure_readiness as readiness

    monkeypatch.setattr(readiness.settings, "environment", environment)
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        configured_ids,
    )
    monkeypatch.setattr(
        readiness,
        "SessionLocal",
        _session_scope(empty_bootstrap_session),
    )

    try:
        exit_code = readiness.main(["bootstrap-empty-preflight"])
    except SystemExit as exc:
        exit_code = int(exc.code)

    output = capsys.readouterr().out
    assert exit_code == 2
    payload = json.loads(output)
    assert expected_code in [blocker["code"] for blocker in payload["blockers"]]


@pytest.mark.parametrize(
    "configured_ids, sensitive_marker",
    [
        (["sensitive-invalid-provider-id-" + ("x" * 161)], "sensitive-invalid-provider-id"),
        (["sensitive-duplicate-id", "sensitive-duplicate-id"], "sensitive-duplicate-id"),
        ([f"sensitive-over-limit-{index}" for index in range(65)], "sensitive-over-limit"),
    ],
    ids=("provider-id-too-long", "duplicate-provider-id", "inventory-too-large"),
)
def test_preflight_cli_rejects_invalid_official_config_without_disclosure_or_writes(
    legacy_pricing_session,
    monkeypatch,
    capsys,
    configured_ids: list[str],
    sensitive_marker: str,
) -> None:
    """An invalid signed inventory must fail before the one-shot registry window."""
    from scripts.ops import pricing_closure_readiness as readiness

    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        configured_ids,
    )
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )
    monkeypatch.setattr(
        readiness,
        "SessionLocal",
        _session_scope(legacy_pricing_session),
    )
    before = _legacy_snapshot(legacy_pricing_session)

    exit_code = readiness.main(["preflight"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 2
    assert sensitive_marker not in output
    assert (
        "OFFICIAL_DOUBAO_CONFIG_INVALID",
        (),
    ) in {(blocker["code"], tuple(blocker["record_ids"])) for blocker in payload["blockers"]}
    assert _legacy_snapshot(legacy_pricing_session) == before


def test_audit_cli_on_0031_returns_schema_not_ready_without_querying_new_tables(
    legacy_pricing_session,
    monkeypatch,
    capsys,
) -> None:
    """Removing the schema guard would make audit crash on 0036-0038 structures."""
    from scripts.ops import pricing_closure_readiness as readiness

    monkeypatch.setattr(
        readiness,
        "SessionLocal",
        _session_scope(legacy_pricing_session),
    )
    before = _legacy_snapshot(legacy_pricing_session)

    exit_code = readiness.main(["audit"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["ready"] is False
    assert payload["migration_revision"] == _LEGACY_MIGRATION_REVISION
    assert [blocker["code"] for blocker in payload["blockers"]] == ["SCHEMA_NOT_READY"]
    assert _legacy_snapshot(legacy_pricing_session) == before


def test_preflight_0031_blocks_even_inactive_tenant_rate_before_0032(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Relaxing this check would make migration 0032 abort before changing any rate."""
    legacy_pricing_session.execute(
        text(
            """
            INSERT INTO credit_rates
                (id, tenant_id, capability, unit, credits_per_unit, is_active, effective_at)
            VALUES
                ('legacy-tenant-rate', 'tenant-a', 'publish', 'call', 1, 0, CURRENT_TIMESTAMP)
            """
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert ("MIGRATION_0032_TENANT_RATE_OVERRIDE", ("legacy-tenant-rate",)) in {
        (blocker.code, blocker.record_ids) for blocker in report.blockers
    }


def test_preflight_0031_blocks_platform_rate_cas_drift_before_0032(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Accepting image=11 would make the 0032 compare-and-swap migration abort."""
    legacy_pricing_session.execute(
        text("UPDATE credit_rates SET credits_per_unit = 11 WHERE id = 'legacy-image'")
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert ("MIGRATION_0032_RATE_MISMATCH", ("legacy-image",)) in {
        (blocker.code, blocker.record_ids) for blocker in report.blockers
    }


def test_preflight_0031_blocks_platform_rate_cas_drift_before_0035(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Accepting reverse_prompt=31 would make migration 0035 abort mid-release."""
    legacy_pricing_session.execute(
        text("UPDATE credit_rates SET credits_per_unit = 31 WHERE id = 'legacy-reverse'")
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert ("MIGRATION_0035_RATE_MISMATCH", ("legacy-reverse",)) in {
        (blocker.code, blocker.record_ids) for blocker in report.blockers
    }


def test_preflight_0031_blocks_duplicate_active_rates_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Dropping this check would let migration 0037 fail while creating unique indexes."""
    legacy_pricing_session.execute(
        text(
            """
            INSERT INTO credit_rates
                (id, tenant_id, capability, unit, credits_per_unit, is_active, effective_at)
            VALUES
                ('legacy-publish-a', NULL, 'publish', 'call', 1, 1, CURRENT_TIMESTAMP),
                ('legacy-publish-b', NULL, 'publish', 'call', 2, 1, CURRENT_TIMESTAMP)
            """
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "MIGRATION_0037_DUPLICATE_ACTIVE_RATE",
        ("legacy-publish-a", "legacy-publish-b"),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_blocks_invalid_usage_amount_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Allowing negative quantity would make migration 0037 reject the production row."""
    legacy_pricing_session.execute(
        text(
            "INSERT INTO usage_records (id, quantity, credits) "
            "VALUES ('legacy-invalid-usage', -1, 0)"
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "MIGRATION_0037_INVALID_USAGE_AMOUNT",
        ("legacy-invalid-usage",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_blocks_invalid_rate_amount_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Allowing a negative inactive rate would make migration 0037 reject the row."""
    legacy_pricing_session.execute(
        text(
            """
            INSERT INTO credit_rates
                (id, tenant_id, capability, unit, credits_per_unit, is_active, effective_at)
            VALUES
                ('legacy-invalid-rate', NULL, 'publish', 'call', -1, 0, CURRENT_TIMESTAMP)
            """
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "MIGRATION_0037_INVALID_RATE_AMOUNT",
        ("legacy-invalid-rate",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_blocks_unexpected_platform_rate_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Accepting voice_clone=31 would make migration 0037 reject its CAS input."""
    legacy_pricing_session.execute(
        text(
            """
            INSERT INTO credit_rates
                (id, tenant_id, capability, unit, credits_per_unit, is_active, effective_at)
            VALUES
                ('legacy-voice-clone', NULL, 'voice_clone', 'call', 31, 1, CURRENT_TIMESTAMP)
            """
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "MIGRATION_0037_PLATFORM_RATE_DRIFT",
        ("legacy-voice-clone",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_blocks_pricing_seed_id_collision_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """An occupied 0371 ID would collide when migration 0037 seeds script pricing."""
    legacy_pricing_session.execute(
        text(
            """
            INSERT INTO credit_rates
                (id, tenant_id, capability, unit, credits_per_unit, is_active, effective_at)
            VALUES
                ('00000000-0000-0000-0000-000000000371', NULL, 'publish', 'call', 1, 0,
                 CURRENT_TIMESTAMP)
            """
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "MIGRATION_0037_SEED_ID_CONFLICT",
        ("00000000-0000-0000-0000-000000000371",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_blocks_active_zero_rate_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """An active zero avatar rate is numeric but still forbidden by migration 0037."""
    legacy_pricing_session.execute(
        text("UPDATE credit_rates SET credits_per_unit = 0 WHERE id = 'legacy-avatar'")
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "MIGRATION_0037_ACTIVE_ZERO_RATE",
        ("legacy-avatar",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_blocks_unknown_historic_voice_without_disclosing_provider_id(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Removing legacy inventory scanning would let an unclassified voice reach migration."""
    legacy_pricing_session.execute(
        text("INSERT INTO provider_configs (id, config) VALUES (:id, :config)"),
        {
            "id": "legacy-provider-config",
            "config": json.dumps(
                {
                    "api_key": "preflight-must-not-print-this-secret",
                    "used_speaker_ids": {"historic-provider-id": "official"},
                }
            ),
        },
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)
    output = json.dumps(report.as_dict(), ensure_ascii=False)

    assert (
        "UNKNOWN_HISTORIC_DOUBAO_ID",
        ("legacy-provider-config",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}
    assert "historic-provider-id" not in output
    assert "preflight-must-not-print-this-secret" not in output


def test_preflight_0031_blocks_wallet_reserved_mismatch_before_billing_migration(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Ignoring the mismatch would make the post-migration billing audit fail."""
    legacy_pricing_session.execute(
        text("UPDATE subscriptions SET quota_credits_reserved = 5 WHERE id = 'legacy-subscription'")
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_WALLET_RESERVED_MISMATCH",
        ("legacy-subscription",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_blocks_nonfinite_wallet_without_disclosing_value(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    legacy_pricing_session.execute(
        text(
            "UPDATE subscriptions SET quota_credits_total = 'NaN' WHERE id = 'legacy-subscription'"
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)
    output = json.dumps(report.as_dict(), ensure_ascii=False)

    assert (
        "PREFLIGHT_WALLET_DOMAIN_FAILURE",
        ("legacy-subscription",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}
    assert "NaN" not in output


def test_preflight_0031_blocks_reserved_usage_without_subscription(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """A reserved legacy usage row without a wallet cannot be reconciled safely."""
    legacy_pricing_session.execute(
        text(
            """
            INSERT INTO usage_records
                (id, subscription_id, quantity, credits, status)
            VALUES ('orphan-reservation', NULL, 1, 2, 'reserved')
            """
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_RESERVED_USAGE_WITHOUT_SUBSCRIPTION",
        ("orphan-reservation",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_reports_missing_legacy_schema_instead_of_crashing(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Without the structural guard a damaged old database would raise OperationalError."""
    legacy_pricing_session.execute(text("DROP TABLE credit_rates"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("credit_rates",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_requires_reasoning_wallets_before_0033(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """A missing wallet table would make migration 0033 fail before its constraint drop."""
    legacy_pricing_session.execute(text("DROP TABLE reasoning_wallets"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("reasoning_wallets",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_requires_named_reasoning_wallet_check_before_0033(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Migration 0033 drops the legacy available-balance check by its exact name."""
    legacy_pricing_session.execute(
        text("ALTER TABLE reasoning_wallets RENAME TO reasoning_wallets_with_check")
    )
    legacy_pricing_session.execute(
        text(
            """
            CREATE TABLE reasoning_wallets (
                tenant_id VARCHAR(36) PRIMARY KEY,
                available_credits INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    )
    legacy_pricing_session.execute(text("DROP TABLE reasoning_wallets_with_check"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("reasoning_wallets.ck_reasoning_wallets_available_nonnegative",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_requires_chat_message_fk_target_before_0034(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Migration 0034 cannot create its source-message FK without the target table."""
    legacy_pricing_session.execute(text("DROP TABLE chat_messages"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("chat_messages",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


@pytest.mark.parametrize("table_name", ["tenants", "users"])
def test_preflight_0031_requires_identity_fk_targets_for_later_migrations(
    legacy_pricing_session,
    monkeypatch,
    table_name: str,
) -> None:
    """The 0034, 0036, and 0038 migrations all create identity foreign keys."""
    legacy_pricing_session.execute(text(f"DROP TABLE {table_name}"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        (table_name,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_requires_asset_fk_target_before_0038(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Manual voice orders cannot be added without the source-audio target table."""
    legacy_pricing_session.execute(text("DROP TABLE assets"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("assets",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_requires_named_admin_audit_check_before_0038(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Migration 0038 replaces the admin action check by its exact name."""
    legacy_pricing_session.execute(
        text("ALTER TABLE admin_audit_logs RENAME TO admin_audit_logs_with_check")
    )
    legacy_pricing_session.execute(
        text(
            "CREATE TABLE admin_audit_logs "
            "(id VARCHAR(36) PRIMARY KEY, action VARCHAR(64) NOT NULL)"
        )
    )
    legacy_pricing_session.execute(text("DROP TABLE admin_audit_logs_with_check"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("admin_audit_logs.ck_admin_audit_logs_action",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_requires_named_pricing_checks_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Migration 0037 drops its legacy pricing checks by exact constraint name."""
    legacy_pricing_session.execute(text("ALTER TABLE credit_rates RENAME TO old_credit_rates"))
    legacy_pricing_session.execute(
        text(
            """
            CREATE TABLE credit_rates (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36),
                capability VARCHAR(64) NOT NULL,
                unit VARCHAR(32) NOT NULL,
                credits_per_unit NUMERIC(12, 4) NOT NULL,
                is_active BOOLEAN NOT NULL,
                effective_at DATETIME NOT NULL
            )
            """
        )
    )
    legacy_pricing_session.execute(text("INSERT INTO credit_rates SELECT * FROM old_credit_rates"))
    legacy_pricing_session.execute(text("DROP TABLE old_credit_rates"))
    legacy_pricing_session.execute(text("ALTER TABLE usage_records RENAME TO old_usage_records"))
    legacy_pricing_session.execute(
        text(
            """
            CREATE TABLE usage_records (
                id VARCHAR(36) PRIMARY KEY,
                subscription_id VARCHAR(36),
                capability VARCHAR(64) NOT NULL DEFAULT 'publish',
                unit VARCHAR(32) NOT NULL DEFAULT 'call',
                quantity NUMERIC(12, 3) NOT NULL,
                credits NUMERIC(18, 6) NOT NULL,
                status VARCHAR(32)
            )
            """
        )
    )
    legacy_pricing_session.execute(
        text("INSERT INTO usage_records SELECT * FROM old_usage_records")
    )
    legacy_pricing_session.execute(text("DROP TABLE old_usage_records"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)
    blocker_pairs = {(blocker.code, blocker.record_ids) for blocker in report.blockers}

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("credit_rates.ck_credit_rates_capability",),
    ) in blocker_pairs
    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        (
            "usage_records.ck_usage_records_capability",
            "usage_records.ck_usage_records_unit",
        ),
    ) in blocker_pairs


def test_preflight_0031_requires_credit_rate_insert_columns_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Migration 0037 seeds new platform rows with an explicit effective timestamp."""
    legacy_pricing_session.execute(text("ALTER TABLE credit_rates DROP COLUMN effective_at"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("credit_rates.effective_at",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0031_requires_usage_constraint_columns_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Migration 0037 replaces capability and unit checks on the existing columns."""
    legacy_pricing_session.execute(text("DROP TABLE usage_records"))
    legacy_pricing_session.execute(
        text(
            """
            CREATE TABLE usage_records (
                id VARCHAR(36) PRIMARY KEY,
                subscription_id VARCHAR(36),
                quantity NUMERIC(12, 3) NOT NULL,
                credits NUMERIC(18, 6) NOT NULL,
                status VARCHAR(32)
            )
            """
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("usage_records.capability", "usage_records.unit"),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0034_requires_complete_cooldown_table_output(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """A 0034 stamp without its created table cannot be trusted by later releases."""
    legacy_pricing_session.execute(
        text("UPDATE alembic_version SET version_num = '20260805_0034'")
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("aibrain_user_cooldowns",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0034_requires_cooldown_relational_contract(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """A stamped 0034 table must retain its FKs, user uniqueness, and lookup indexes."""
    legacy_pricing_session.execute(
        text("UPDATE alembic_version SET version_num = '20260805_0034'")
    )
    _install_0034_cooldown_table(legacy_pricing_session, relational_contract=False)
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)
    blocker_pairs = {(blocker.code, blocker.record_ids) for blocker in report.blockers}

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        (
            "aibrain_user_cooldowns.foreign_key:source_message_id->chat_messages.id",
            "aibrain_user_cooldowns.foreign_key:tenant_id->tenants.id",
            "aibrain_user_cooldowns.foreign_key:user_id->users.id",
        ),
    ) in blocker_pairs
    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("aibrain_user_cooldowns.uq_aibrain_user_cooldowns_user_id",),
    ) in blocker_pairs
    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        (
            "aibrain_user_cooldowns.ix_aibrain_user_cooldowns_tenant_expires",
            "aibrain_user_cooldowns.ix_aibrain_user_cooldowns_tenant_id",
        ),
    ) in blocker_pairs


def test_register_official_cli_on_0031_fails_closed_before_registry_queries(
    legacy_pricing_session,
    monkeypatch,
    capsys,
) -> None:
    """Without this guard an operator could invoke registration before table 0038 exists."""
    from scripts.ops import pricing_closure_readiness as readiness

    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["signed-official-voice"],
    )
    monkeypatch.setattr(
        readiness,
        "SessionLocal",
        _session_scope(legacy_pricing_session),
    )
    before = _legacy_snapshot(legacy_pricing_session)

    exit_code = readiness.main(["register-official"])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "SCHEMA_NOT_READY",
        "migration_revision": _LEGACY_MIGRATION_REVISION,
        "target_migration_revision": _RELEASE_MIGRATION_REVISION,
    }
    assert _legacy_snapshot(legacy_pricing_session) == before


def test_preflight_requires_production_environment_for_fail_closed_routing(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """A local/default environment would silently disable customer routing safeguards."""
    from scripts.ops import pricing_closure_readiness as readiness

    monkeypatch.setattr(readiness.settings, "environment", "local")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["signed-official-voice"],
    )
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )

    report = readiness.pricing_closure_preflight(
        legacy_pricing_session,
        production_mode=True,
    )

    assert "PRODUCTION_ENVIRONMENT_REQUIRED" in {blocker.code for blocker in report.blockers}


def test_preflight_requires_nonempty_signed_official_voice_list(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """An empty exact list cannot classify the platform-owned historic voice safely."""
    from scripts.ops import pricing_closure_readiness as readiness

    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        [],
    )
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )

    report = readiness.pricing_closure_preflight(
        legacy_pricing_session,
        production_mode=True,
    )

    assert "OFFICIAL_DOUBAO_CONFIG_MISSING" in {blocker.code for blocker in report.blockers}


def test_preflight_rejects_revision_outside_approved_release_line(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """An unrelated or future revision must not be treated as safe by string ordering."""
    legacy_pricing_session.execute(text("UPDATE alembic_version SET version_num = '20990101_9999'"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "UNSUPPORTED_SCHEMA_REVISION",
        ("20990101_9999",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0036_requires_billing_table_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Trusting a 0036 stamp without its billing table would make migration 0037 crash."""
    legacy_pricing_session.execute(text("UPDATE alembic_version SET version_num = '20260829_0036'"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("billing_operations",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0036_blocks_invalid_billing_amount_before_0037(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """Letting a negative billing amount pass would make migration 0037 abort mid-release."""
    legacy_pricing_session.execute(text("UPDATE alembic_version SET version_num = '20260829_0036'"))
    _install_complete_0036_billing_schema(legacy_pricing_session)
    legacy_pricing_session.execute(
        text(
                """
                INSERT INTO billing_operations
                    (id, tenant_id, user_id, operation, idempotency_key, request_hash,
                     quote_hash, pricing_snapshot, requested_credits, settled_credits,
                     released_credits, status, created_at, updated_at)
                VALUES ('invalid-billing-operation', 'tenant-a', 'user-a', 'video_create',
                        'invalid-billing-operation', 'a', 'b', '{}', -1, 0, 0,
                        'in_progress', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
        )
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "MIGRATION_0037_INVALID_BILLING_AMOUNT",
        ("invalid-billing-operation",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0037_requires_0036_billing_schema_before_0038(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """A false 0037 stamp must not reach 0038 or the full ORM audit."""
    legacy_pricing_session.execute(text("UPDATE alembic_version SET version_num = '20260829_0037'"))
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("billing_operations",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_0036_requires_complete_billing_operation_columns(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """A partial billing ledger cannot be trusted merely because its amount columns exist."""
    legacy_pricing_session.execute(
        text("UPDATE alembic_version SET version_num = '20260829_0036'")
    )
    _install_minimal_0036_billing_schema(legacy_pricing_session)
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)
    missing_ids = {
        record_id
        for blocker in report.blockers
        if blocker.code == "PREFLIGHT_SCHEMA_MISSING"
        for record_id in blocker.record_ids
    }

    assert {
        "billing_operations.tenant_id",
        "billing_operations.user_id",
        "billing_operations.operation",
        "billing_operations.idempotency_key",
        "billing_operations.request_hash",
        "billing_operations.quote_hash",
        "billing_operations.pricing_snapshot",
        "billing_operations.status",
        "billing_operations.completion_kind",
        "billing_operations.completed_at",
        "billing_operations.created_at",
        "billing_operations.updated_at",
    } <= missing_ids


def test_preflight_0036_requires_named_billing_and_usage_contracts(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """A 0036 stamp cannot substitute for the named ledger constraints 0037 relies on."""
    legacy_pricing_session.execute(
        text("UPDATE alembic_version SET version_num = '20260829_0036'")
    )
    _install_complete_0036_billing_schema(legacy_pricing_session)
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)
    missing_ids = {
        record_id
        for blocker in report.blockers
        if blocker.code == "PREFLIGHT_SCHEMA_MISSING"
        for record_id in blocker.record_ids
    }

    assert {
        "billing_operations.ck_billing_operations_state",
        "billing_operations.ck_billing_operations_zero_price",
        "billing_operations.ck_billing_operations_completion",
        "billing_operations.ck_billing_operations_amounts_finite",
        "billing_operations.ck_billing_operations_amounts_nonnegative",
        "usage_records.ck_usage_records_billing_allocation",
        "usage_records.uq_usage_records_billing_operation_item_index",
        "billing_operations.foreign_key:tenant_id->tenants.id",
        "billing_operations.foreign_key:user_id->users.id",
        "usage_records.foreign_key:billing_operation_id->billing_operations.id",
    } <= missing_ids


def test_preflight_0037_requires_named_rate_guards_and_active_rate_indexes(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """A 0037 stamp must prove both its numeric guards and partial unique indexes exist."""
    legacy_pricing_session.execute(
        text("UPDATE alembic_version SET version_num = '20260829_0037'")
    )
    _install_complete_0036_billing_schema(legacy_pricing_session)
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)
    missing_ids = {
        record_id
        for blocker in report.blockers
        if blocker.code == "PREFLIGHT_SCHEMA_MISSING"
        for record_id in blocker.record_ids
    }

    assert {
        "credit_rates.ck_credit_rates_credits_per_unit_valid",
        "usage_records.ck_usage_records_amounts_valid",
        "credit_rates.uq_credit_rates_platform_active_capability_unit",
        "credit_rates.uq_credit_rates_tenant_active_capability_unit",
    } <= missing_ids


def test_preflight_0033_rejects_a_retained_0032_wallet_constraint(
    legacy_pricing_session,
    monkeypatch,
) -> None:
    """0033 cannot safely drop a constraint that a forged 0033 stamp claims is gone."""
    legacy_pricing_session.execute(
        text("UPDATE alembic_version SET version_num = '20260805_0033'")
    )
    legacy_pricing_session.commit()

    report = _legacy_preflight_report(legacy_pricing_session, monkeypatch)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("reasoning_wallets.ck_reasoning_wallets_available_nonnegative",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_target_audit_fails_closed_for_missing_manual_delivery_table(db_session) -> None:
    """A final 0038 version stamp must not hide a missing manual-delivery relation."""
    from scripts.ops import pricing_closure_readiness as readiness

    db_session.execute(text("DROP TABLE credit_refund_grants"))
    db_session.commit()

    report = readiness.pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "PREFLIGHT_SCHEMA_MISSING",
        ("credit_refund_grants",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_target_schema_contract_matches_current_metadata(db_session) -> None:
    """Keep the release model and the target-schema reflection gate in lockstep."""
    from scripts.ops import pricing_closure_readiness as readiness

    blockers = tuple(
        (blocker.code, blocker.record_ids)
        for blocker in readiness._target_schema_preflight_blockers(db_session)
    )

    assert blockers == ()


_PLATFORM_PARTIAL_INDEX = "uq_credit_rates_platform_active_capability_unit"
_TENANT_PARTIAL_INDEX = "uq_credit_rates_tenant_active_capability_unit"
_RENEWAL_PARTIAL_INDEX = "uq_brand_voice_orders_awaiting_renewal_per_voice"
_PARTIAL_INDEX_CONTRACTS = (
    (
        _PLATFORM_PARTIAL_INDEX,
        ("capability", "unit"),
        "tenant_id IS NULL AND is_active",
    ),
    (
        _TENANT_PARTIAL_INDEX,
        ("tenant_id", "capability", "unit"),
        "tenant_id IS NOT NULL AND is_active",
    ),
    (
        _RENEWAL_PARTIAL_INDEX,
        ("existing_brand_voice_id",),
        "order_type = 'renew' AND status = 'awaiting_fulfillment'",
    ),
)
_PARTIAL_INDEX_COLUMNS = {
    index_name: columns for index_name, columns, _predicate in _PARTIAL_INDEX_CONTRACTS
}


def _reflected_partial_index_blockers(
    monkeypatch,
    *,
    index_name: str,
    columns: tuple[str, ...],
    where_clause: object,
    present: bool = True,
    reflected_columns: tuple[str, ...] | None = None,
    reflected_unique: bool = True,
):
    """Drive the real schema blocker with only SQLAlchemy reflection replaced."""
    from scripts.ops import pricing_closure_readiness as readiness

    class ReflectedIndexInspector:
        @staticmethod
        def get_table_names():
            return ["contract_table"]

        @staticmethod
        def get_columns(_table_name):
            return [{"name": column} for column in columns]

        @staticmethod
        def get_indexes(_table_name):
            if not present:
                return []
            return [
                {
                    "name": index_name,
                    "column_names": list(reflected_columns or columns),
                    "unique": reflected_unique,
                    "dialect_options": {"postgresql_where": where_clause},
                }
            ]

    class ReflectedSession:
        @staticmethod
        def connection():
            return object()

    monkeypatch.setattr(readiness, "inspect", lambda _connection: ReflectedIndexInspector())
    return readiness._schema_contract_blockers(
        ReflectedSession(),
        required_schema={"contract_table": set(columns)},
        named_checks={},
        named_uniques={},
        named_indexes={"contract_table": {index_name: (columns, True)}},
        foreign_keys={},
    )


@pytest.mark.parametrize(
    ("index_name", "columns", "_expected_predicate"),
    _PARTIAL_INDEX_CONTRACTS,
)
@pytest.mark.parametrize(
    "reflected_predicate",
    [contract[2] for contract in _PARTIAL_INDEX_CONTRACTS],
)
def test_partial_unique_predicates_cannot_be_reused_across_indexes(
    monkeypatch,
    index_name: str,
    columns: tuple[str, ...],
    _expected_predicate: str,
    reflected_predicate: str,
) -> None:
    """Binding any of the three valid predicates to the wrong index must fail closed."""
    blockers = _reflected_partial_index_blockers(
        monkeypatch,
        index_name=index_name,
        columns=columns,
        where_clause=reflected_predicate,
    )

    expected_record = f"contract_table.{index_name}"
    if reflected_predicate == _expected_predicate:
        assert blockers == []
    else:
        assert [(blocker.code, blocker.record_ids) for blocker in blockers] == [
            ("PREFLIGHT_SCHEMA_MISSING", (expected_record,))
        ]


@pytest.mark.parametrize(
    ("present", "reflected_columns", "reflected_unique"),
    [
        (False, ("existing_brand_voice_id",), True),
        (True, ("wrong_column",), True),
        (True, ("existing_brand_voice_id",), False),
    ],
)
def test_partial_unique_index_requires_presence_columns_and_uniqueness(
    monkeypatch,
    present: bool,
    reflected_columns: tuple[str, ...],
    reflected_unique: bool,
) -> None:
    """Predicate compatibility must not weaken the existing index-shape gate."""
    index_name = _RENEWAL_PARTIAL_INDEX
    blockers = _reflected_partial_index_blockers(
        monkeypatch,
        index_name=index_name,
        columns=("existing_brand_voice_id",),
        where_clause="order_type = 'renew' AND status = 'awaiting_fulfillment'",
        present=present,
        reflected_columns=reflected_columns,
        reflected_unique=reflected_unique,
    )

    assert [(blocker.code, blocker.record_ids) for blocker in blockers] == [
        ("PREFLIGHT_SCHEMA_MISSING", (f"contract_table.{index_name}",))
    ]


@pytest.mark.parametrize(
    ("index_name", "where_clause"),
    [
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "order_type = 'RENEW' AND status = 'awaiting_fulfillment'",
            id="literal-value-case",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "order_type = 'renew' AND status = 'Awaiting_fulfillment'",
            id="second-literal-value-case",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "order_type = 'renew::text' AND status = 'awaiting_fulfillment'",
            id="cast-text-inside-literal",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "order_type = 'renew' OR status = 'awaiting_fulfillment'",
            id="or",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "NOT (order_type = 'renew' AND status = 'awaiting_fulfillment')",
            id="not",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "order_type = 'renew' AND "
            "(status = 'awaiting_fulfillment' OR status = 'fulfilled')",
            id="nested-or",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "order_type = 'renew'",
            id="missing-condition",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "order_type = 'renew' AND status = 'awaiting_fulfillment' "
            "AND tenant_id IS NOT NULL",
            id="extra-filter",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "order_type = 'renew' AND status = 'awaiting_fulfillment' AND TRUE",
            id="and-true",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "(order_type)::citext = 'renew'::citext AND "
            "(status)::text = 'awaiting_fulfillment'::text",
            id="citext-cast",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "(order_type)::char(5) = 'renew'::char(5) AND "
            "(status)::text = 'awaiting_fulfillment'::text",
            id="char-cast",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "(order_type)::varchar(5) = 'renew'::varchar(5) AND "
            "(status)::text = 'awaiting_fulfillment'::text",
            id="varchar-cast",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "(order_type)::integer = 'renew'::text AND "
            "(status)::text = 'awaiting_fulfillment'::text",
            id="integer-cast",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "lower(order_type) = 'renew' AND status = 'awaiting_fulfillment'",
            id="function-call",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            '"ORDER_TYPE" = \'renew\' AND status = \'awaiting_fulfillment\'',
            id="quoted-uppercase-column",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "order_type = 'renew' AND status = 'awaiting_fulfillment' trailing_garbage",
            id="trailing-garbage",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            "(",
            id="unbalanced-parenthesis",
        ),
        pytest.param(
            _RENEWAL_PARTIAL_INDEX,
            None,
            id="missing-dialect-value",
        ),
        pytest.param(
            _PLATFORM_PARTIAL_INDEX,
            "tenant_id IS NULL AND NOT is_active",
            id="not-active",
        ),
        pytest.param(
            _PLATFORM_PARTIAL_INDEX,
            "tenant_id IS NULL AND is_active IS FALSE",
            id="is-false",
        ),
        pytest.param(
            _PLATFORM_PARTIAL_INDEX,
            "tenant_id IS NULL AND is_active IS NOT FALSE",
            id="is-not-false",
        ),
        pytest.param(
            _PLATFORM_PARTIAL_INDEX,
            "tenant_id IS NULL AND COALESCE(is_active, TRUE)",
            id="coalesce",
        ),
    ],
)
def test_partial_unique_predicates_reject_unsafe_or_drifted_sql(
    monkeypatch,
    index_name: str,
    where_clause: object,
) -> None:
    """Substring matching, global lowercasing, or blind cast removal makes this matrix fail."""
    blockers = _reflected_partial_index_blockers(
        monkeypatch,
        index_name=index_name,
        columns=_PARTIAL_INDEX_COLUMNS[index_name],
        where_clause=where_clause,
    )

    assert [(blocker.code, blocker.record_ids) for blocker in blockers] == [
        ("PREFLIGHT_SCHEMA_MISSING", (f"contract_table.{index_name}",))
    ]


@pytest.mark.parametrize(
    "where_clause",
    [
        "((order_type)::text = 'renew'::text) "
        "AND ((status)::text = 'awaiting_fulfillment'::text)",
        "(((status)::TEXT = ('awaiting_fulfillment'::text))) AnD "
        "(((order_type)::text = ('renew'::TEXT)))",
    ],
)
def test_manual_renewal_predicate_accepts_only_supported_postgresql_text_casts(
    monkeypatch,
    where_clause: str,
) -> None:
    """PostgreSQL's harmless text casts and grouping must not create a false blocker."""
    blockers = _reflected_partial_index_blockers(
        monkeypatch,
        index_name="uq_brand_voice_orders_awaiting_renewal_per_voice",
        columns=("existing_brand_voice_id",),
        where_clause=where_clause,
    )

    assert blockers == []


@pytest.mark.skipif(
    not os.getenv("PRICING_READINESS_POSTGRES_URL"),
    reason="set PRICING_READINESS_POSTGRES_URL to run PostgreSQL reflection coverage",
)
def test_postgresql_preflight_accepts_reflected_manual_renewal_partial_index() -> None:
    """Exercise the production PostgreSQL 16 ::text reflection through the real blocker path."""
    from scripts.ops import pricing_closure_readiness as readiness

    engine = create_engine(os.environ["PRICING_READINESS_POSTGRES_URL"], future=True)
    schema = f"pricing_readiness_partial_index_{uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with Session(engine) as session:
            session.execute(text(f'SET search_path TO "{schema}"'))
            session.execute(
                text(
                    """
                    CREATE TABLE brand_voice_orders (
                        existing_brand_voice_id VARCHAR(36),
                        order_type VARCHAR(32) NOT NULL,
                        status VARCHAR(32) NOT NULL
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    CREATE UNIQUE INDEX uq_brand_voice_orders_awaiting_renewal_per_voice
                    ON brand_voice_orders (existing_brand_voice_id)
                    WHERE order_type = 'renew' AND status = 'awaiting_fulfillment'
                    """
                )
            )
            session.commit()

            reflected_index = next(
                index
                for index in inspect(session.connection()).get_indexes("brand_voice_orders")
                if index["name"] == "uq_brand_voice_orders_awaiting_renewal_per_voice"
            )
            reflected_where = str(reflected_index["dialect_options"]["postgresql_where"])
            assert "::text" in reflected_where

            blockers = readiness._schema_contract_blockers(
                session,
                required_schema={
                    "brand_voice_orders": {
                        "existing_brand_voice_id",
                        "order_type",
                        "status",
                    }
                },
                named_checks={},
                named_uniques={},
                named_indexes={
                    "brand_voice_orders": {
                        "uq_brand_voice_orders_awaiting_renewal_per_voice": (
                            ("existing_brand_voice_id",),
                            True,
                        )
                    }
                },
                foreign_keys={},
            )

            assert blockers == []
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("PRICING_READINESS_POSTGRES_URL"),
    reason="set PRICING_READINESS_POSTGRES_URL to run PostgreSQL reflection coverage",
)
def test_postgresql_preflight_reflects_named_wallet_constraint() -> None:
    """Keep the named-check verification real on PostgreSQL, not only SQLite reflection."""
    from scripts.ops import pricing_closure_readiness as readiness

    engine = create_engine(os.environ["PRICING_READINESS_POSTGRES_URL"], future=True)
    schema = f"pricing_readiness_{uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with Session(engine) as session:
            session.execute(text(f'SET search_path TO "{schema}"'))
            session.execute(
                text(
                    """
                    CREATE TABLE reasoning_wallets (
                        tenant_id VARCHAR(36) PRIMARY KEY,
                        available_credits INTEGER NOT NULL,
                        CONSTRAINT ck_reasoning_wallets_available_nonnegative
                            CHECK (available_credits >= 0)
                    )
                    """
                )
            )
            blockers = readiness._legacy_schema_preflight_blockers(
                session,
                migration_revision="20260805_0033",
            )
            assert (
                "PREFLIGHT_SCHEMA_MISSING",
                ("reasoning_wallets.ck_reasoning_wallets_available_nonnegative",),
            ) in {(blocker.code, blocker.record_ids) for blocker in blockers}
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_preflight_on_target_schema_runs_the_complete_audit(
    db_session,
    monkeypatch,
) -> None:
    """A final revision stamp must not hide corrupt release-schema billing state."""
    from scripts.ops import pricing_closure_readiness as readiness

    db_session.add(
        BillingOperation(
            id="target-schema-corrupt-operation",
            tenant_id="tenant-a",
            user_id="user-a",
            operation="video_create",
            idempotency_key="target-schema-corrupt-operation",
            request_hash="a" * 64,
            quote_hash="b" * 64,
            pricing_snapshot={},
            requested_credits=Decimal("1"),
            settled_credits=Decimal("0"),
            released_credits=Decimal("0"),
            status="in_progress",
        )
    )
    db_session.commit()
    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["configured-official"],
    )
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )

    report = readiness.pricing_closure_preflight(
        db_session,
        production_mode=True,
    )

    assert report.ready is False
    assert (
        "BILLING_OPERATION_INVARIANT_FAILURE",
        ("target-schema-corrupt-operation",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_preflight_cli_on_target_schema_redacts_provider_ids(
    db_session,
    monkeypatch,
    capsys,
) -> None:
    """Delegating target-schema preflight to audit must keep the CLI output safe."""
    from scripts.ops import pricing_closure_readiness as readiness

    sensitive_provider_id = "target-schema-sensitive-official-id"
    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        [sensitive_provider_id],
    )
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_voice_clone_speaker_ids",
        [],
    )
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))

    exit_code = readiness.main(["preflight"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    mismatch = next(
        blocker
        for blocker in payload["blockers"]
        if blocker["code"] == "OFFICIAL_DOUBAO_REGISTRY_MISMATCH"
    )
    assert exit_code == 2
    assert sensitive_provider_id not in output
    assert mismatch["record_ids"] == []
    assert mismatch["record_count"] == 1


def test_target_audit_rejects_invalid_official_config_without_disclosure(
    db_session,
    monkeypatch,
) -> None:
    """The full gate must reject an inventory that registration cannot normalize."""
    from scripts.ops import pricing_closure_readiness as readiness

    invalid_provider_id = "target-sensitive-invalid-provider-id-" + ("x" * 161)
    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        [invalid_provider_id],
    )

    report = readiness.pricing_closure_readiness(db_session, production_mode=True)
    payload = readiness._readiness_cli_payload(report)

    assert (
        "OFFICIAL_DOUBAO_CONFIG_INVALID",
        (),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}
    assert invalid_provider_id not in json.dumps(payload)


def test_register_official_rejects_partially_populated_official_registry(
    db_session,
    monkeypatch,
    capsys,
    pricing_closure_snapshot,
) -> None:
    """Filling a partial registry would hide evidence of an interrupted first registration."""
    from scripts.ops import pricing_closure_readiness as readiness

    db_session.add(
        BrandVoiceProviderId(
            id="partial-official-row",
            provider="doubao-voice-clone",
            normalized_provider_id="official-a",
            kind="official",
            status="active",
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["official-a", "official-b"],
    )
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))
    before = pricing_closure_snapshot(db_session)

    exit_code = readiness.main(["register-official"])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"error": "OFFICIAL_REGISTRATION_ABORTED"}
    assert pricing_closure_snapshot(db_session) == before


def test_register_official_rejects_unrelated_pricing_audit_blocker(
    db_session,
    monkeypatch,
    capsys,
    pricing_closure_snapshot,
) -> None:
    """Registration must not proceed while any non-registry pricing blocker remains."""
    from scripts.ops import pricing_closure_readiness as readiness

    db_session.add(
        CreditRate(
            id="registration-invalid-rate",
            capability="image",
            unit="image",
            credits_per_unit=Decimal("0"),
            is_active=False,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["official-only"],
    )
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))
    before = pricing_closure_snapshot(db_session)

    exit_code = readiness.main(["register-official"])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"error": "OFFICIAL_REGISTRATION_ABORTED"}
    assert pricing_closure_snapshot(db_session) == before


def test_register_official_rejects_nonproduction_environment_without_mutation(
    db_session,
    monkeypatch,
    capsys,
    pricing_closure_snapshot,
) -> None:
    """Calling the production writer from a local environment must fail before mutation."""
    from scripts.ops import pricing_closure_readiness as readiness

    monkeypatch.setattr(readiness.settings, "environment", "local")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["official-only"],
    )
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))
    before = pricing_closure_snapshot(db_session)

    exit_code = readiness.main(["register-official"])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"error": "OFFICIAL_REGISTRATION_ABORTED"}
    assert pricing_closure_snapshot(db_session) == before


def test_register_official_rejects_customer_registry_overlap_before_writer(
    db_session,
    monkeypatch,
    capsys,
    pricing_closure_snapshot,
    seed_pricing_closure_state,
) -> None:
    """Treating a customer-owned provider ID as official must not reach the writer."""
    from scripts.ops import pricing_closure_readiness as readiness

    sensitive = seed_pricing_closure_state(db_session)
    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        [sensitive["customer_provider_id"]],
    )
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))
    before = pricing_closure_snapshot(db_session)

    exit_code = readiness.main(["register-official"])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert json.loads(output) == {"error": "OFFICIAL_REGISTRATION_ABORTED"}
    assert sensitive["customer_provider_id"] not in output
    assert pricing_closure_snapshot(db_session) == before


def test_registration_fixture_is_a_terminal_doubao_fulfillment_replay(
    db_session,
    monkeypatch,
    seed_pricing_closure_state,
) -> None:
    """Registration readiness must be proven against a real 30,000-credit order."""
    from app.core.config import settings
    from app.services.brand_voice_orders import resolve_brand_voice_order

    sensitive = seed_pricing_closure_state(db_session)
    actor = db_session.get(User, "user-a")
    actor.role = "admin"
    db_session.commit()
    monkeypatch.setattr(settings, "engine_platform_tenant_slugs", {"billing-a"})
    order = db_session.scalar(select(BrandVoiceOrder).where(BrandVoiceOrder.status == "fulfilled"))

    replay = resolve_brand_voice_order(
        db_session,
        actor=actor,
        order_id=order.id,
        action="fulfill",
        provider_voice_id=sensitive["customer_provider_id"],
        now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    )

    db_session.expire_all()
    operation = db_session.get(BillingOperation, order.billing_operation_id)
    usage = db_session.scalar(
        select(UsageRecord).where(UsageRecord.billing_operation_id == operation.id)
    )
    assert replay.id == order.id
    assert operation.operation == "doubao_brand_voice_order_create"
    assert operation.status == "completed"
    assert operation.completion_kind == "succeeded"
    assert operation.requested_credits == Decimal("30000")
    assert operation.settled_credits == Decimal("30000")
    assert operation.released_credits == Decimal("0")
    assert usage.status == "settled"
    assert usage.credits == Decimal("30000")
    assert usage.provider == "doubao-voice-clone"


def test_readiness_fails_on_unknown_historic_doubao_id(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    db_session.add(
        ProviderConfig(
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={"used_speaker_ids": {"unknown-id": "historic"}},
            is_active=False,
        )
    )
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=True)
    blockers = tuple((blocker.code, blocker.record_ids) for blocker in report.blockers)

    assert report.ready is False
    assert report.blockers[0].code == "UNKNOWN_HISTORIC_DOUBAO_ID", blockers
    assert report.blockers[0].record_ids == ("unknown-id",)


def test_readiness_audit_is_read_only_and_redacts_provider_config(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    config = ProviderConfig(
        capability="voice_clone",
        provider="doubao-voice-clone",
        config={"api_key": "must-never-be-disclosed", "speaker_ids": ["legacy-id"]},
        is_active=False,
    )
    voice = BrandVoice(
        tenant_id="tenant-a",
        owner_user_id="user-a",
        name="Cosy",
        provider="cosyvoice-voice-clone",
        speaker_id="cosy-not-doubao",
        status="ready",
        consent_confirmed=True,
    )
    db_session.add_all([config, voice])
    db_session.commit()
    before = (
        len(db_session.scalars(select(ProviderConfig)).all()),
        len(db_session.scalars(select(BrandVoice)).all()),
    )

    report = pricing_closure_readiness(db_session, production_mode=False)
    payload = report.as_dict()

    after = (
        len(db_session.scalars(select(ProviderConfig)).all()),
        len(db_session.scalars(select(BrandVoice)).all()),
    )
    assert after == before
    assert "must-never-be-disclosed" not in str(payload)
    assert "cosy-not-doubao" not in {
        item.provider_voice_id for item in report.inventory_unknown_ids
    }


def test_readiness_reports_invalid_and_duplicate_active_rates_with_record_ids(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    # Historical databases can contain duplicates that modern partial indexes prevent.
    db_session.execute(text("DROP INDEX uq_credit_rates_platform_active_capability_unit"))
    db_session.add_all(
        [
            CreditRate(
                id="zero-rate",
                capability="image",
                unit="image",
                credits_per_unit=Decimal("0"),
                is_active=True,
            ),
            CreditRate(
                id="duplicate-rate-a",
                capability="avatar",
                unit="second",
                credits_per_unit=Decimal("1"),
                is_active=True,
            ),
            CreditRate(
                id="duplicate-rate-b",
                capability="avatar",
                unit="second",
                credits_per_unit=Decimal("2"),
                is_active=True,
            ),
        ]
    )
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=True)

    assert {(item.code, item.record_ids) for item in report.blockers} >= {
        ("INVALID_CREDIT_RATE", ("zero-rate",)),
        ("DUPLICATE_ACTIVE_CREDIT_RATE", ("duplicate-rate-a", "duplicate-rate-b")),
    }


def test_readiness_blocks_approved_platform_rate_drift(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    drifted = CreditRate(
        id="drifted-approved-platform-rate",
        tenant_id=None,
        capability="image",
        unit="image",
        credits_per_unit=Decimal("1.0000"),
        is_active=True,
    )
    db_session.add(drifted)
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "APPROVED_PLATFORM_RATE_MISMATCH",
        (drifted.id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_readiness_cli_duplicate_rates_emit_json_blocker_with_both_ids(
    db_session,
    monkeypatch,
    capsys,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    db_session.execute(text("DROP INDEX uq_credit_rates_platform_active_capability_unit"))
    db_session.add_all(
        [
            CreditRate(
                id="cli-duplicate-rate-a",
                capability="avatar",
                unit="second",
                credits_per_unit=Decimal("1"),
                is_active=True,
            ),
            CreditRate(
                id="cli-duplicate-rate-b",
                capability="avatar",
                unit="second",
                credits_per_unit=Decimal("2"),
                is_active=True,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))

    exit_code = readiness.main([])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert (
        "DUPLICATE_ACTIVE_CREDIT_RATE",
        ("cli-duplicate-rate-a", "cli-duplicate-rate-b"),
    ) in {(blocker["code"], tuple(blocker["record_ids"])) for blocker in payload["blockers"]}


def test_readiness_reports_official_config_registry_mismatch_and_retired_slot_surface(
    db_session,
    monkeypatch,
) -> None:
    from app.services import provider_voice_registry
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    monkeypatch.setattr(
        provider_voice_registry.settings,
        "engine_doubao_official_voice_ids",
        ["official-only"],
    )

    report = pricing_closure_readiness(db_session, production_mode=True)

    assert ("OFFICIAL_DOUBAO_REGISTRY_MISMATCH", ("official-only",)) in {
        (item.code, item.record_ids) for item in report.blockers
    }
    assert [(item.surface, item.state) for item in report.legacy_slot_write_surfaces] == [
        ("admin_api:POST /tenants/{tenant_id}/voice-slots", "retired")
    ]


def test_readiness_fails_closed_when_policy_default_or_fallback_drifts(
    db_session,
    monkeypatch,
) -> None:
    from app.services import pricing
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    monkeypatch.setitem(
        pricing.PRICING_POLICIES,
        "script_generate",
        replace(pricing.PRICING_POLICIES["script_generate"], default_unit_credits=Decimal("2")),
    )
    monkeypatch.setitem(pricing.DEFAULT_RATE_CREDITS, ("avatar", "second"), Decimal("999"))

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert ("PRICING_POLICY_DEFAULT_MISMATCH", ("script_generate",)) in {
        (item.code, item.record_ids) for item in report.blockers
    }
    assert ("DEFAULT_RATE_FALLBACK_MISMATCH", ("avatar/second",)) in {
        (item.code, item.record_ids) for item in report.blockers
    }


def test_readiness_lists_safe_platform_and_tenant_rate_rows(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    db_session.add_all(
        [
            CreditRate(
                id="platform-rate-row",
                capability="image",
                unit="image",
                credits_per_unit=Decimal("1"),
                is_active=True,
            ),
            CreditRate(
                id="tenant-rate-row",
                tenant_id="tenant-a",
                capability="scene_prompt",
                unit="call",
                credits_per_unit=Decimal("2"),
                is_active=True,
            ),
        ]
    )
    db_session.commit()

    payload = pricing_closure_readiness(db_session, production_mode=False).as_dict()

    assert payload["platform_rates"] == [
        {
            "id": "platform-rate-row",
            "scope": "platform",
            "tenant_id": None,
            "capability": "image",
            "unit": "image",
            "credits_per_unit": "1.0000",
            "active": True,
        }
    ]
    assert payload["tenant_rates"] == [
        {
            "id": "tenant-rate-row",
            "scope": "tenant",
            "tenant_id": "tenant-a",
            "capability": "scene_prompt",
            "unit": "call",
            "credits_per_unit": "2.0000",
            "active": True,
        }
    ]


def test_readiness_cli_default_audit_emits_json_and_rolls_back(
    db_session,
    monkeypatch,
    capsys,
    pricing_closure_snapshot,
    seed_pricing_closure_state,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    sensitive = seed_pricing_closure_state(db_session)
    before = pricing_closure_snapshot(db_session)
    assert {table_name for table_name, rows in before.items() if not rows} == {
        "credit_refund_grants"
    }
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))

    exit_code = readiness.main([])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 2
    assert payload["production_mode"] is True
    assert sensitive["secret"] not in output
    assert sensitive["source_url"] not in output
    assert sensitive["customer_provider_id"] not in output
    assert "exact-config-content" not in output
    assert "usage-trace-secret" not in output
    assert pricing_closure_snapshot(db_session) == before


@pytest.mark.parametrize(
    ("rate_id", "stored_value"),
    [
        ("historic-nan-rate-cli", "NaN"),
        ("historic-infinity-rate-cli", "Infinity"),
        ("historic-negative-infinity-rate-cli", "-Infinity"),
        ("historic-negative-rate-cli", "-1"),
        ("historic-zero-rate-cli", "0"),
    ],
)
def test_readiness_cli_corrupt_rate_emits_machine_readable_blocker(
    db_session,
    monkeypatch,
    capsys,
    rate_id: str,
    stored_value: str,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    db_session.execute(text("PRAGMA ignore_check_constraints = ON"))
    db_session.execute(
        text(
            "INSERT INTO credit_rates "
            "(id, capability, unit, credits_per_unit, is_active, effective_at) "
            "VALUES (:id, 'image', 'image', :value, 0, CURRENT_TIMESTAMP)"
        ),
        {"id": rate_id, "value": stored_value},
    )
    db_session.execute(text("PRAGMA ignore_check_constraints = OFF"))
    db_session.commit()
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))

    exit_code = readiness.main([])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 2
    assert ("INVALID_CREDIT_RATE", (rate_id,)) in {
        (blocker["code"], tuple(blocker["record_ids"])) for blocker in payload["blockers"]
    }
    assert payload["platform_rates"] == [
        {
            "id": rate_id,
            "scope": "platform",
            "tenant_id": None,
            "capability": "image",
            "unit": "image",
            "credits_per_unit": "invalid",
            "active": False,
        }
    ]
    if stored_value in {"NaN", "Infinity", "-Infinity"}:
        assert stored_value not in output


def test_readiness_cli_unknown_inventory_aborts_without_mutation(
    db_session,
    monkeypatch,
    capsys,
    pricing_closure_snapshot,
    seed_pricing_closure_state,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    sensitive = seed_pricing_closure_state(db_session)
    db_session.add(
        ProviderConfig(
            id="cli-unknown-history-config",
            capability="voice_clone",
            provider="doubao-voice-clone",
            config={"used_speaker_ids": {"unknown-cli-id": "historic"}},
            is_active=False,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["cli-allowed-official-id"],
    )
    before = pricing_closure_snapshot(db_session)
    before_registry_ids = {row[0] for row in before["provider_voice_registry"]}
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))

    exit_code = readiness.main(["register-official"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 2
    assert payload == {"error": "OFFICIAL_REGISTRATION_ABORTED"}
    assert "unknown-cli-id" not in output
    assert sensitive["secret"] not in output
    assert sensitive["source_url"] not in output
    after = pricing_closure_snapshot(db_session)
    assert after == before
    assert {row[0] for row in after["provider_voice_registry"]} == before_registry_ids


def test_register_official_cli_exact_list_has_only_expected_delta_and_rejects_repeat(
    db_session,
    monkeypatch,
    capsys,
    pricing_closure_snapshot,
    seed_pricing_closure_state,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    sensitive = seed_pricing_closure_state(db_session)
    allowed_ids = ("cli-official-a", "cli-official-b")
    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        list(reversed(allowed_ids)),
    )
    monkeypatch.setattr(readiness, "SessionLocal", _session_scope(db_session))
    before = pricing_closure_snapshot(db_session)
    before_registry = {row[2]: row for row in before["provider_voice_registry"]}
    assert tuple(before_registry) == (sensitive["customer_provider_id"],)
    initial_blockers = tuple(
        (blocker.code, blocker.record_ids)
        for blocker in readiness.pricing_closure_readiness(
            db_session,
            production_mode=True,
        ).blockers
    )

    first_exit_code = readiness.main(["register-official"])

    first_output = capsys.readouterr().out
    first_payload = json.loads(first_output)
    assert first_exit_code == 0, initial_blockers
    assert first_payload == {"registered_official_count": len(allowed_ids)}
    for forbidden in (
        *allowed_ids,
        sensitive["secret"],
        sensitive["source_url"],
        sensitive["customer_provider_id"],
        sensitive["other_provider"],
        "exact-config-content",
        "usage-trace-secret",
    ):
        assert forbidden not in first_output

    after_first = pricing_closure_snapshot(db_session)
    for table, rows in before.items():
        if table != "provider_voice_registry":
            assert after_first[table] == rows
    after_registry = {row[2]: row for row in after_first["provider_voice_registry"]}
    assert set(after_registry) == {
        sensitive["customer_provider_id"],
        *allowed_ids,
    }
    assert (
        after_registry[sensitive["customer_provider_id"]]
        == before_registry[sensitive["customer_provider_id"]]
    )
    for provider_voice_id in allowed_ids:
        row = after_registry[provider_voice_id]
        assert row[1:7] == (
            "doubao-voice-clone",
            provider_voice_id,
            "official",
            None,
            None,
            "active",
        )

    def fail_if_writer_repeats(*_args, **_kwargs):
        raise AssertionError("official registry writer must run exactly once")

    monkeypatch.setattr(
        readiness.provider_voice_registry,
        "register_official_provider_voice_ids",
        fail_if_writer_repeats,
    )
    second_exit_code = readiness.main(["register-official"])

    second_output = capsys.readouterr().out
    assert second_exit_code == 2
    assert json.loads(second_output) == {"error": "OFFICIAL_REGISTRATION_ALREADY_COMPLETE"}
    assert pricing_closure_snapshot(db_session) == after_first
    for forbidden in (
        *allowed_ids,
        sensitive["secret"],
        sensitive["source_url"],
        sensitive["customer_provider_id"],
        sensitive["other_provider"],
    ):
        assert forbidden not in second_output


def test_register_official_cli_redacts_writer_failure_and_rolls_back(
    db_session,
    monkeypatch,
    capsys,
    pricing_closure_snapshot,
    seed_pricing_closure_state,
) -> None:
    from scripts.ops import pricing_closure_readiness as readiness

    seed_pricing_closure_state(db_session)
    sensitive_marker = "sensitive-provider-id-from-writer-failure"
    monkeypatch.setattr(readiness.settings, "environment", "production")
    monkeypatch.setattr(
        readiness.settings,
        "engine_doubao_official_voice_ids",
        ["writer-failure-official"],
    )
    before = pricing_closure_snapshot(db_session)

    class NoisyRollbackSession:
        def __getattr__(self, name):
            return getattr(db_session, name)

        def rollback(self):
            db_session.rollback()
            print(sensitive_marker)
            print(sensitive_marker, file=sys.stderr)
            raise RuntimeError(sensitive_marker)

    @contextmanager
    def failing_session_scope():
        yield NoisyRollbackSession()

    monkeypatch.setattr(readiness, "SessionLocal", failing_session_scope)

    def fail_writer(db, *, provider_voice_ids):
        assert provider_voice_ids == ("writer-failure-official",)
        print(sensitive_marker)
        print(sensitive_marker, file=sys.stderr)
        db.add(
            BrandVoiceProviderId(
                id="writer-failure-uncommitted-row",
                provider="doubao-voice-clone",
                normalized_provider_id="writer-failure-official",
                kind="official",
                status="active",
            )
        )
        db.flush()
        raise RuntimeError(sensitive_marker)

    monkeypatch.setattr(
        readiness.provider_voice_registry,
        "register_official_provider_voice_ids",
        fail_writer,
    )

    exit_code = readiness.main(["register-official"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.out) == {"error": "OFFICIAL_REGISTRATION_FAILED"}
    assert sensitive_marker not in captured.out
    assert sensitive_marker not in captured.err
    assert pricing_closure_snapshot(db_session) == before


@pytest.mark.parametrize(
    "rate_id, value",
    [("historic-negative-rate", "-1"), ("historic-nan-rate", "NaN")],
)
def test_readiness_blocks_historically_corrupt_rate_values(
    db_session, rate_id: str, value: str
) -> None:
    """SQLite's check bypass models a legacy row written before current constraints."""
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    db_session.execute(text("PRAGMA ignore_check_constraints = ON"))
    db_session.execute(
        text(
            "INSERT INTO credit_rates "
            "(id, capability, unit, credits_per_unit, is_active, effective_at) "
            "VALUES (:id, 'image', 'image', :value, 0, CURRENT_TIMESTAMP)"
        ),
        {"id": rate_id, "value": value},
    )
    db_session.execute(text("PRAGMA ignore_check_constraints = OFF"))
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=True)

    assert ("INVALID_CREDIT_RATE", (rate_id,)) in {
        (blocker.code, blocker.record_ids) for blocker in report.blockers
    }


def test_readiness_reports_persisted_operation_and_wallet_invariants_without_repair(
    db_session,
) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    operation = BillingOperation(
        id="corrupt-operation",
        tenant_id="tenant-a",
        user_id="user-a",
        operation="video_create",
        idempotency_key="corrupt-operation",
        request_hash="a" * 64,
        quote_hash="b" * 64,
        pricing_snapshot={},
        requested_credits=Decimal("0"),
        settled_credits=Decimal("0"),
        released_credits=Decimal("0"),
        status="in_progress",
    )
    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_reserved = 7
    db_session.add(operation)
    db_session.execute(text("PRAGMA ignore_check_constraints = ON"))
    db_session.commit()
    db_session.execute(text("PRAGMA ignore_check_constraints = OFF"))
    before = (
        operation.status,
        operation.pricing_snapshot,
        subscription.quota_credits_reserved,
    )

    report = pricing_closure_readiness(db_session, production_mode=True)

    assert report.ready is False
    assert {(blocker.code, blocker.record_ids) for blocker in report.blockers} >= {
        ("BILLING_OPERATION_INVARIANT_FAILURE", (operation.id,)),
        ("BILLING_WALLET_INVARIANT_FAILURE", (subscription.id,)),
    }
    db_session.expire_all()
    after_operation = db_session.get(BillingOperation, operation.id)
    after_subscription = db_session.get(Subscription, subscription.id)
    assert (
        after_operation.status,
        after_operation.pricing_snapshot,
        after_subscription.quota_credits_reserved,
    ) == before


def test_target_audit_rejects_wallet_used_below_operation_settlements(
    db_session,
    seed_pricing_closure_state,
) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    seed_pricing_closure_state(db_session)
    subscription = db_session.get(Subscription, "subscription-a")
    assert subscription.quota_credits_used == 30_000
    subscription.quota_credits_used = 0
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "BILLING_WALLET_INVARIANT_FAILURE",
        (subscription.id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_target_audit_rejects_negative_wallet_amount(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_used = -1
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "BILLING_WALLET_INVARIANT_FAILURE",
        (subscription.id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_target_audit_rejects_wallet_total_below_zero(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_total = -1
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "BILLING_WALLET_INVARIANT_FAILURE",
        (subscription.id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_target_audit_rejects_nonfinite_wallet_without_crashing(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    db_session.execute(
        text(
            "UPDATE subscriptions SET quota_credits_total = 'Infinity' WHERE id = 'subscription-a'"
        )
    )
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "BILLING_WALLET_INVARIANT_FAILURE",
        ("subscription-a",),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_target_audit_rejects_used_plus_reserved_above_total(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    subscription = db_session.get(Subscription, "subscription-a")
    subscription.quota_credits_total = 100
    subscription.quota_credits_used = 60
    subscription.quota_credits_reserved = 41
    db_session.add(
        UsageRecord(
            id="wallet-domain-reservation",
            tenant_id=subscription.tenant_id,
            subscription_id=subscription.id,
            capability="image",
            provider="legacy",
            unit="image",
            quantity=Decimal("1"),
            credits=Decimal("41"),
            cost_cents=0,
            status="reserved",
        )
    )
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "BILLING_WALLET_INVARIANT_FAILURE",
        (subscription.id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_target_audit_rejects_duplicate_active_subscription(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    source = db_session.get(Subscription, "subscription-a")
    duplicate = Subscription(
        id="duplicate-active-subscription",
        tenant_id=source.tenant_id,
        plan_id=source.plan_id,
        status="active",
        period_start=source.period_start,
        period_end=source.period_end,
        quota_credits_total=100,
        quota_credits_used=0,
        quota_credits_reserved=0,
    )
    db_session.add(duplicate)
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "BILLING_DUPLICATE_ACTIVE_SUBSCRIPTION",
        tuple(sorted((source.id, duplicate.id))),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_target_audit_rejects_order_backlink_to_non_order_operation(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    operation = _reserve_zero_price_operation(db_session)
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    asset = Asset(
        id="orphan-order-audio",
        tenant_id="tenant-a",
        type="audio",
        source="upload",
        storage_key="tenants/tenant-a/orphan-order.wav",
        mime_type="audio/wav",
        duration_ms=10_000,
        status="ready",
    )
    order = BrandVoiceOrder(
        id="orphan-order",
        tenant_id="tenant-a",
        user_id="user-a",
        order_type="create",
        requested_name="Orphan order",
        source_audio_asset_id=asset.id,
        source_metadata_snapshot={},
        consent_confirmed_at=now,
        billing_operation_id=operation.id,
        status="awaiting_fulfillment",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([asset, order])
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "BILLING_OPERATION_INVARIANT_FAILURE",
        (operation.id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}


def test_target_audit_rejects_refund_grant_without_rejected_order(db_session) -> None:
    from scripts.ops.pricing_closure_readiness import pricing_closure_readiness

    operation = _reserve_zero_price_operation(db_session)
    grant = CreditRefundGrant(
        id="orphan-refund-grant",
        billing_operation_id=operation.id,
        tenant_id=operation.tenant_id,
        user_id=operation.user_id,
        source_subscription_id="subscription-a",
        amount_credits=Decimal("30000"),
        status="pending",
    )
    db_session.add(grant)
    db_session.commit()

    report = pricing_closure_readiness(db_session, production_mode=False)

    assert (
        "BILLING_REFUND_GRANT_INVARIANT_FAILURE",
        (grant.id,),
    ) in {(blocker.code, blocker.record_ids) for blocker in report.blockers}
