from fastapi.testclient import TestClient

from app.main import app
from app.workers.celery_app import celery_app


def test_demo_task_can_run_eagerly(auth_context) -> None:
    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/tasks/demo",
            json={"message": "from-test"},
            headers=auth_context["headers"],
        )
        assert response.status_code == 202
        task_id = response.json()["data"]["task_id"]

        status_response = client.get(f"/api/v1/tasks/{task_id}", headers=auth_context["headers"])
        assert status_response.status_code == 200
        body = status_response.json()["data"]
        assert body["status"] == "SUCCESS"
        assert body["result"] == {"message": "from-test", "status": "processed"}
    finally:
        celery_app.conf.task_always_eager = previous


def test_anonymous_demo_task_rejected() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/tasks/demo", json={"message": "from-test"})
    assert response.status_code == 401
