from fastapi.testclient import TestClient

from app.main import app


def test_validation_errors_use_response_envelope() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/tasks/demo", json={"message": ""})
    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["request_id"]
