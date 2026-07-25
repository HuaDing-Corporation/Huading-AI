from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app


def test_image_history_delete_route_is_registered() -> None:
    delete_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v1/history/images/{category}/{history_id}"
        and "DELETE" in route.methods
    ]

    assert len(delete_routes) == 1


def test_image_history_clear_route_is_registered() -> None:
    delete_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v1/history/images"
        and "DELETE" in route.methods
    ]

    assert len(delete_routes) == 1


def test_image_history_clear_requires_a_category(auth_context) -> None:
    response = TestClient(app).delete(
        "/api/v1/history/images",
        headers=auth_context["headers"],
    )

    assert response.status_code == 422
