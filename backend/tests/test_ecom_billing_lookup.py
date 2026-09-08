import os
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import get_db_session, get_object_storage
from app.core.security import create_access_token
from app.db.models import (
    Asset,
    BillingOperation,
    ProviderConfig,
    Subscription,
    TaskAsset,
    Tenant,
    UsageRecord,
    User,
    VideoTask,
)
from app.main import app
from app.services.billing_operations import BillingInProgressLookup
from app.services.ecom_billing import try_finalize_ecom_operation


@pytest.fixture(params=["sqlite", "postgres"])
def auth_db(auth_db, request, cloned_model_metadata_factory):
    if request.param == "sqlite":
        yield auth_db
        return
    raw_url = os.getenv("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("TEST_POSTGRES_URL is required for isolated PostgreSQL lookup tests")
    url = make_url(raw_url)
    if url.host not in {"127.0.0.1", "localhost"} or not str(url.database).startswith(
        "huading_pricing_test_"
    ):
        pytest.fail("lookup tests require a local huading_pricing_test_* database")
    engine = create_engine(url)
    schema = f"pending_lookup_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    metadata = cloned_model_metadata_factory()
    metadata.create_all(scoped)
    factory = sessionmaker(bind=scoped, autoflush=False)
    previous = app.dependency_overrides[get_db_session]

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db_session] = override_db
    try:
        yield factory
    finally:
        app.dependency_overrides[get_db_session] = previous
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@contextmanager
def _read_only_requests(factory):
    engine = factory.kw["bind"]
    writes = []

    def authorize(action, table, _column, _database, _trigger):
        if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
            writes.append((action, table))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def read_only_transaction(connection):
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")

    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            driver = connection.connection.driver_connection
            driver.set_authorizer(authorize)
    else:
        event.listen(engine, "begin", read_only_transaction)
    try:
        yield writes
    finally:
        if engine.dialect.name == "sqlite":
            driver.set_authorizer(None)
        else:
            event.remove(engine, "begin", read_only_transaction)


def _snapshot(factory):
    with factory() as db:
        return deepcopy(
            {
                model.__tablename__: list(db.execute(select(model.__table__).order_by(model.id)))
                for model in (BillingOperation, Subscription, UsageRecord, VideoTask, Asset)
            }
        )


@pytest.fixture
def submitted_batch(auth_db, auth_context, monkeypatch):
    from app.api.v1.routes import ecom_images

    class Storage:
        def delete_object(self, _key):
            pass

    monkeypatch.setattr(ecom_images, "_enqueue_image_task", lambda _task: None)
    app.dependency_overrides[get_object_storage] = Storage
    with auth_db() as db:
        db.add(
            ProviderConfig(
                capability="image",
                provider="apimart",
                is_active=True,
                config={"api_key": "lookup-test-key"},
            )
        )
        db.add(
            Asset(
                id="lookup-source",
                tenant_id=auth_context["tenant_id"],
                type="product_image",
                source="upload",
                status="ready",
                storage_key=f"tenants/{auth_context['tenant_id']}/uploads/source.png",
                mime_type="image/png",
            )
        )
        db.commit()
    client = TestClient(app)

    def submit(kind="cutout", count=1):
        item = {"source_asset_id": "lookup-source"}
        if kind == "model":
            item["gender"] = "male"
        payload = item if count == 1 else {"items": [item] * count}
        estimate = client.post(
            f"/api/v1/ecom-images/{kind}/estimate",
            json=payload,
            headers=auth_context["headers"],
        )
        assert estimate.status_code == 200, estimate.text
        key = str(uuid4())
        endpoint = f"/api/v1/ecom-images/{kind}" + ("/batch" if count > 1 else "")
        response = client.post(
            endpoint,
            json=payload,
            headers={
                **auth_context["headers"],
                "Idempotency-Key": key,
                "X-Huading-Quote": estimate.json()["data"]["quote_token"],
            },
        )
        assert response.status_code == 202, response.text
        with auth_db() as db:
            operation = db.scalars(
                select(BillingOperation).where(BillingOperation.idempotency_key == key)
            ).one()
            task_ids = list(
                db.scalars(
                    select(UsageRecord.video_task_id)
                    .where(UsageRecord.billing_operation_id == operation.id)
                    .order_by(UsageRecord.billing_item_index)
                )
            )
            return {
                "url": f"/api/v1/billing/operations/by-idempotency/ecom_{kind}/{key}",
                "operation_id": operation.id,
                "batch_id": operation.result_id,
                "task_ids": task_ids,
                "key": key,
            }

    try:
        yield submit
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.parametrize("kind", ["cutout", "model"])
@pytest.mark.parametrize(
    "states", [("queued",), ("running",), ("done", "running"), ("done", "failed")]
)
def test_pending_batch_lookup_is_read_only_and_not_a_terminal_result(
    auth_db, auth_context, submitted_batch, kind, states
):
    batch = submitted_batch(kind, len(states))
    with auth_db() as db:
        for task_id, status in zip(batch["task_ids"], states, strict=True):
            db.get(VideoTask, task_id).status = status
        db.commit()
    before = _snapshot(auth_db)
    client = TestClient(app)
    with _read_only_requests(auth_db) as writes:
        for _ in range(3):
            response = client.get(batch["url"], headers=auth_context["headers"])
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["state"] == "in_progress"
            assert data["completion_kind"] is data["result"] is data["failure"] is None
            assert data["billing"] == {
                "operation_id": batch["operation_id"],
                "idempotency_key": batch["key"],
                "status": "reserved",
                "requested_credits": 80 * len(states),
                "held_credits": 80 * len(states),
                "settled_credits": 0,
                "released_credits": 0,
            }
            assert data["result_type"] == "ecom_image_batch"
            assert data["result_id"] == batch["batch_id"]
            assert data["resource"] is None
    assert writes == []
    assert _snapshot(auth_db) == before


def _finish_tasks(factory, batch, states):
    with factory() as db:
        for task_id, state in zip(batch["task_ids"], states, strict=True):
            task = db.get(VideoTask, task_id)
            task.status = state
            if state == "done":
                asset = Asset(
                    tenant_id=task.tenant_id,
                    type="generated_image",
                    source="generated",
                    status="ready",
                    mime_type="image/png",
                    storage_key=f"tenants/{task.tenant_id}/photos/{task.id}/output.png",
                )
                db.add(asset)
                db.flush()
                db.add(TaskAsset(video_task_id=task.id, asset_id=asset.id, role="output_image"))
        db.commit()


@pytest.mark.parametrize("kind", ["cutout", "model"])
@pytest.mark.parametrize("states", [("done", "done"), ("done", "failed"), ("failed", "failed")])
def test_terminal_batch_lookup_preserves_settlement_and_failure_contract(
    auth_db, auth_context, submitted_batch, kind, states
):
    batch = submitted_batch(kind, 2)
    _finish_tasks(auth_db, batch, states)
    with auth_db() as db:
        assert try_finalize_ecom_operation(db, billing_operation_id=batch["operation_id"])
        db.commit()
    before = _snapshot(auth_db)
    settled = states.count("done") * 80
    with _read_only_requests(auth_db) as writes:
        for _ in range(3):
            response = TestClient(app).get(batch["url"], headers=auth_context["headers"])
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["state"] == "completed"
            assert data["billing"]["held_credits"] == 0
            assert data["billing"]["settled_credits"] == settled
            assert data["billing"]["released_credits"] == 160 - settled
            if settled:
                assert data["completion_kind"] == "succeeded"
                assert data["failure"] is None
                assert data["resource"] == data["result"]
                assert [item["status"] for item in data["result"]["items"]] == list(states)
                assert [item["task_id"] for item in data["result"]["items"]] == batch["task_ids"]
            else:
                assert data["completion_kind"] == "failed"
                assert data["result"] is data["resource"] is data["result_id"] is None
                assert data["failure"]["code"] == "ECOM_IMAGE_BATCH_FAILED"
    assert writes == []
    assert _snapshot(auth_db) == before
    with auth_db() as db:
        subscription = db.scalars(select(Subscription)).one()
        assert subscription.quota_credits_used == settled
        assert subscription.quota_credits_reserved == 0


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_type",
        "unknown_type",
        "missing_id",
        "invalid_id",
        "noncanonical_id",
        "foreign_batch",
        "stored_result",
        "missing_task",
        "duplicate_task",
        "foreign_task",
        "other_user_task",
        "task_batch",
        "task_operation",
        "task_kind",
        "task_mode",
        "item_index",
        "bool_index",
        "empty_params",
        "null_params",
        "usage_tenant",
    ],
)
def test_pending_batch_rejects_corrupt_identity_without_writes(
    auth_db, auth_context, submitted_batch, corruption
):
    batch = submitted_batch("cutout", 2)
    with auth_db() as db:
        operation = db.get(BillingOperation, batch["operation_id"])
        task = db.get(VideoTask, batch["task_ids"][0])
        usages = list(
            db.scalars(
                select(UsageRecord)
                .where(UsageRecord.billing_operation_id == operation.id)
                .order_by(UsageRecord.billing_item_index)
            )
        )
        db.add(Tenant(id="foreign-tenant", slug="foreign-tenant", name="Foreign"))
        db.flush()
        db.add(
            User(
                id="other-user",
                tenant_id=auth_context["tenant_id"],
                role="admin",
                email="other@example.com",
                password_hash="hash",
            )
        )
        db.flush()
        if corruption == "missing_type":
            operation.result_type = None
        elif corruption == "unknown_type":
            operation.result_type = "unknown_result"
        elif corruption == "missing_id":
            operation.result_id = None
        elif corruption == "invalid_id":
            operation.result_id = "invalid-batch"
        elif corruption == "noncanonical_id":
            operation.result_id = uuid4().hex
            for task_id in batch["task_ids"]:
                item = db.get(VideoTask, task_id)
                item.params = {**item.params, "batch_id": operation.result_id}
        elif corruption == "foreign_batch":
            operation.result_id = str(uuid4())
        elif corruption == "stored_result":
            operation.result_payload = {
                "items": [
                    {
                        "item_index": 0,
                        "task_id": task.id,
                        "source_asset_id": "lookup-source",
                        "status": "done",
                        "asset_id": None,
                    }
                ]
            }
        elif corruption == "missing_task":
            usages[0].video_task_id = None
            db.flush()
            db.delete(task)
        elif corruption == "duplicate_task":
            usages[1].video_task_id = task.id
        elif corruption == "foreign_task":
            task.tenant_id = "foreign-tenant"
        elif corruption == "other_user_task":
            task.created_by_user_id = "other-user"
        elif corruption == "task_batch":
            task.params = {**task.params, "batch_id": str(uuid4())}
        elif corruption == "task_operation":
            task.params = {**task.params, "billing_operation_id": str(uuid4())}
        elif corruption == "task_kind":
            task.params = {**task.params, "kind": "ecom_model"}
        elif corruption == "task_mode":
            task.mode = "avatar_talk"
        elif corruption == "item_index":
            task.params = {**task.params, "billing_item_index": 1}
        elif corruption == "bool_index":
            task.params = {**task.params, "billing_item_index": False}
            flag_modified(task, "params")
        elif corruption == "empty_params":
            task.params = {}
        elif corruption == "null_params":
            task.params = None
        elif corruption == "usage_tenant":
            usages[0].tenant_id = "foreign-tenant"
        db.commit()
    before = _snapshot(auth_db)
    with _read_only_requests(auth_db) as writes:
        response = TestClient(app).get(batch["url"], headers=auth_context["headers"])
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "BILLING_INVARIANT_VIOLATION"
    assert response.json()["data"] is None
    assert writes == []
    assert _snapshot(auth_db) == before


@pytest.mark.parametrize("corruption", ["missing", "empty", "extra", "pending_item", "unknown"])
def test_completed_batch_still_requires_valid_terminal_payload(
    auth_db, auth_context, submitted_batch, corruption
):
    batch = submitted_batch()
    _finish_tasks(auth_db, batch, ["done"])
    with auth_db() as db:
        try_finalize_ecom_operation(db, billing_operation_id=batch["operation_id"])
        db.commit()
        operation = db.get(BillingOperation, batch["operation_id"])
        if corruption == "missing":
            operation.result_payload = None
        elif corruption == "empty":
            operation.result_payload = {"items": []}
        elif corruption == "extra":
            operation.result_payload = {**operation.result_payload, "unsafe": "must-not-escape"}
        elif corruption == "pending_item":
            payload = deepcopy(operation.result_payload)
            payload["items"][0]["status"] = "running"
            operation.result_payload = payload
        else:
            operation.result_type = "unknown_result"
        db.commit()
    before = _snapshot(auth_db)
    with _read_only_requests(auth_db) as writes:
        response = TestClient(app).get(batch["url"], headers=auth_context["headers"])
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "BILLING_INVARIANT_VIOLATION"
    assert response.json()["data"] is None
    assert writes == []
    assert _snapshot(auth_db) == before


@pytest.mark.parametrize("scope", ["anonymous", "other_user", "other_tenant", "missing_key"])
def test_batch_lookup_authorizes_payment_user_not_just_tenant(
    auth_db, auth_context, submitted_batch, scope
):
    batch = submitted_batch()
    headers = auth_context["headers"]
    if scope == "anonymous":
        headers = {}
    elif scope in {"other_user", "other_tenant"}:
        with auth_db() as db:
            tenant_id = auth_context["tenant_id"]
            if scope == "other_tenant":
                tenant_id = "foreign-tenant"
                db.add(Tenant(id=tenant_id, slug=tenant_id, name="Foreign"))
                db.flush()
            db.add(
                User(
                    id="other-user",
                    tenant_id=tenant_id,
                    role="admin",
                    email="other@example.com",
                    password_hash="hash",
                )
            )
            db.commit()
        token = create_access_token(user_id="other-user", tenant_id=tenant_id, role="admin")
        headers = {"Authorization": f"Bearer {token}"}
    else:
        batch["url"] = batch["url"].replace(batch["key"], str(uuid4()))
    before = _snapshot(auth_db)
    with _read_only_requests(auth_db) as writes:
        response = TestClient(app).get(batch["url"], headers=headers)
    assert response.status_code == (401 if scope == "anonymous" else 404), response.text
    assert response.json()["data"] is None
    assert writes == []
    assert _snapshot(auth_db) == before


def test_read_only_guard_blocks_cte_update(auth_db, auth_context):
    engine = auth_db.kw["bind"]
    table = "subscriptions"
    if engine.dialect.name == "postgresql":
        schema = engine.get_execution_options()["schema_translate_map"][None]
        table = f'"{schema}"."subscriptions"'
    before = _snapshot(auth_db)
    with _read_only_requests(auth_db):
        with pytest.raises(DBAPIError) as caught:
            with auth_db() as db:
                db.execute(
                    text(
                        f"WITH candidate AS (SELECT id FROM {table}) "
                        f"UPDATE {table} SET quota_credits_used = quota_credits_used + 1 "
                        "WHERE id IN (SELECT id FROM candidate)"
                    )
                )
    if engine.dialect.name == "postgresql":
        assert caught.value.orig.sqlstate == "25006"
    else:
        assert caught.value.orig.sqlite_errorcode == sqlite3.SQLITE_AUTH
    assert _snapshot(auth_db) == before


@pytest.mark.parametrize("field,value", [("resource", {"items": []}), ("result_id", "bad-id")])
def test_pending_response_schema_forbids_incompatible_ecom_resource(field, value):
    payload = {
        "operation": "ecom_cutout",
        "idempotency_key": str(uuid4()),
        "result_type": "ecom_image_batch",
        "result_id": str(uuid4()),
        "billing": {
            "operation_id": str(uuid4()),
            "idempotency_key": str(uuid4()),
            "status": "reserved",
            "requested_credits": 80,
            "held_credits": 80,
            "settled_credits": 0,
            "released_credits": 0,
        },
        field: value,
    }
    with pytest.raises(ValidationError):
        BillingInProgressLookup.model_validate(payload)


def test_postgres_lookup_observes_one_snapshot_during_worker_settlement(
    auth_db, auth_context, submitted_batch
):
    engine = auth_db.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("requires independent PostgreSQL read/write connections")
    batch = submitted_batch()
    _finish_tasks(auth_db, batch, ["done"])
    writer_engine = create_engine(engine.url)
    writer = sessionmaker(
        bind=writer_engine.execution_options(
            schema_translate_map=engine.get_execution_options()["schema_translate_map"]
        ),
        autoflush=False,
    )
    committed = []

    def settle_after_parent_read(_conn, _cursor, statement, _params, _context, _many):
        if (
            not committed
            and statement.startswith("SELECT")
            and "billing_operations" in statement
            and "brand_voice_orders" not in statement
        ):
            committed.append(True)
            with writer() as db:
                assert try_finalize_ecom_operation(db, billing_operation_id=batch["operation_id"])
                db.commit()

    event.listen(engine, "after_cursor_execute", settle_after_parent_read)
    try:
        with _read_only_requests(auth_db):
            response = TestClient(app).get(batch["url"], headers=auth_context["headers"])
    finally:
        event.remove(engine, "after_cursor_execute", settle_after_parent_read)
        writer_engine.dispose()
    assert committed == [True]
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "in_progress"
    assert response.json()["data"]["billing"]["held_credits"] == 80
    with _read_only_requests(auth_db):
        terminal = TestClient(app).get(batch["url"], headers=auth_context["headers"])
    assert terminal.status_code == 200, terminal.text
    assert terminal.json()["data"]["completion_kind"] == "succeeded"
    assert terminal.json()["data"]["billing"]["settled_credits"] == 80
