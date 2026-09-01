"""Authentication helpers for state-changing control-plane endpoints.

Webhook delivery deliberately uses its own signed-request verification.  The
operator API uses a bearer-like static key for this prototype; production
deployments should replace it with the merchant's identity provider.
"""

from dataclasses import dataclass
from hmac import compare_digest

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


@dataclass(frozen=True)
class OperatorPrincipal:
    role: str
    identity: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Control-Plane-Key"},
    )


async def require_operator(
    x_control_plane_key: str | None = Header(default=None),
) -> OperatorPrincipal:
    settings = get_settings()
    if not settings.control_plane_api_key:
        if settings.control_plane_auth_required:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CONTROL_PLANE_API_KEY must be configured outside dev/test/local",
            )
        return OperatorPrincipal(role="admin", identity="local-demo")

    is_operator_key = bool(x_control_plane_key) and compare_digest(
        x_control_plane_key, settings.control_plane_api_key
    )
    is_admin_key = bool(settings.control_plane_admin_api_key and x_control_plane_key) and compare_digest(
        x_control_plane_key, settings.control_plane_admin_api_key
    )
    if not is_operator_key and not is_admin_key:
        raise _unauthorized("valid X-Control-Plane-Key required")
    if is_admin_key:
        return OperatorPrincipal(role="admin", identity="control-plane-admin")
    return OperatorPrincipal(role="operator", identity="control-plane-operator")


async def require_admin(
    x_control_plane_key: str | None = Header(default=None),
) -> OperatorPrincipal:
    principal = await require_operator(x_control_plane_key)
    # Local demo has an explicit, documented implicit admin. In deployed
    # environments force-promotion requires a separate privileged key.
    if principal.role == "admin":
        return principal
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="admin control-plane key required",
    )
