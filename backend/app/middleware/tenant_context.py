from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class TenantContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        tenant_id = request.headers.get("X-Tenant-ID")
        request.state.requested_tenant_id = tenant_id
        response = await call_next(request)
        if tenant_id:
            response.headers["X-Tenant-ID"] = tenant_id
        return response
