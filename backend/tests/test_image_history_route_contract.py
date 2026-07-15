from fastapi.routing import APIRoute

from app.main import app


def test_image_history_delete_route_is_not_registered() -> None:
    delete_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v1/history/images/{category}/{history_id}"
        and "DELETE" in route.methods
    ]

    assert delete_routes == []
