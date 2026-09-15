from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import VideoTask


@pytest.fixture
def avatar_task(auth_db, auth_context):
    with auth_db() as db:
        task = VideoTask(
            tenant_id=auth_context["tenant_id"],
            created_by_user_id=auth_context["user_id"],
            status="queued",
            mode="avatar_talk",
            video_mode="avatar_talk",
            params={"avatar_provider": "heygen", "avatar_model": "avatar_iv"},
        )
        db.add(task)
        db.commit()
        return task.id


def test_run_claim_is_exclusive_and_expired_owner_cannot_write(auth_db, auth_context, avatar_task):
    from app.services.avatar_runs import (
        AvatarLeaseLost,
        claim_avatar_run,
        create_avatar_run,
        update_avatar_run,
    )

    now = datetime.now(UTC)
    tenant = auth_context["tenant_id"]
    with auth_db() as db:
        create_avatar_run(db, task=db.get(VideoTask, avatar_task))
        db.commit()
    first = claim_avatar_run(auth_db, tenant_id=tenant, task_id=avatar_task, now=now)
    assert first is not None
    assert claim_avatar_run(auth_db, tenant_id=tenant, task_id=avatar_task, now=now) is None
    later = now + timedelta(hours=1)
    second = claim_avatar_run(auth_db, tenant_id=tenant, task_id=avatar_task, now=later)
    assert second and second != first
    with auth_db() as db:
        with pytest.raises(AvatarLeaseLost):
            update_avatar_run(
                db,
                tenant_id=tenant,
                task_id=avatar_task,
                owner=first,
                now=later,
                provider_job_id="stale-job",
            )
        db.rollback()
        update_avatar_run(
            db,
            tenant_id=tenant,
            task_id=avatar_task,
            owner=second,
            now=later,
            provider_job_id="current-job",
        )
        db.commit()


def test_run_cannot_be_claimed_by_other_tenant_or_after_completion(
    auth_db, auth_context, avatar_task
):
    from app.services.avatar_runs import claim_avatar_run, create_avatar_run, update_avatar_run

    now = datetime.now(UTC)
    tenant = auth_context["tenant_id"]
    with auth_db() as db:
        create_avatar_run(db, task=db.get(VideoTask, avatar_task))
        db.commit()
    assert claim_avatar_run(auth_db, tenant_id="other", task_id=avatar_task, now=now) is None
    owner = claim_avatar_run(auth_db, tenant_id=tenant, task_id=avatar_task, now=now)
    with auth_db() as db:
        update_avatar_run(
            db, tenant_id=tenant, task_id=avatar_task, owner=owner, now=now, state="completed"
        )
        db.commit()
    assert (
        claim_avatar_run(
            auth_db, tenant_id=tenant, task_id=avatar_task, now=now + timedelta(days=1)
        )
        is None
    )


def test_terminal_history_delete_cascades_run(auth_db, avatar_task):
    from app.db.models import AvatarProviderRun
    from app.services.avatar_runs import create_avatar_run

    with auth_db() as db:
        task = db.get(VideoTask, avatar_task)
        create_avatar_run(db, task=task)
        db.commit()
        assert db.scalar(select(AvatarProviderRun)) is not None
        db.delete(task)
        db.commit()
        assert db.scalar(select(AvatarProviderRun)) is None


def test_key_fingerprint_and_job_id_are_immutable(auth_db, auth_context, avatar_task):
    from app.services.avatar_runs import (
        AvatarRunInvariant,
        claim_avatar_run,
        create_avatar_run,
        update_avatar_run,
    )

    now = datetime.now(UTC)
    tenant = auth_context["tenant_id"]
    with auth_db() as db:
        create_avatar_run(db, task=db.get(VideoTask, avatar_task))
        db.commit()
    owner = claim_avatar_run(auth_db, tenant_id=tenant, task_id=avatar_task, now=now)
    with auth_db() as db:
        update_avatar_run(
            db,
            tenant_id=tenant,
            task_id=avatar_task,
            owner=owner,
            now=now,
            provider_job_id="job1",
            request_body={"a": 1},
            request_fingerprint="one",
        )
        db.commit()
        with pytest.raises(AvatarRunInvariant):
            update_avatar_run(
                db,
                tenant_id=tenant,
                task_id=avatar_task,
                owner=owner,
                now=now,
                provider_job_id="job2",
            )
        db.rollback()
        with pytest.raises(AvatarRunInvariant):
            update_avatar_run(
                db,
                tenant_id=tenant,
                task_id=avatar_task,
                owner=owner,
                now=now,
                request_body={"a": 2},
            )
