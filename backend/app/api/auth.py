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
        if settings.is_local_env:
            return OperatorPrincipal(role="admin", identity="local-demo")
        # An open demo is deliberately unauthenticated, but it is not trusted:
        # it gets the operator role only, so `force: true` model promotion
        # still requires a separately configured admin key.
        return OperatorPrincipal(role="operator", identity="open-demo")

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


async def require_keyed_operator(
    x_control_plane_key: str | None = Header(default=None),
) -> OperatorPrincipal:
    """An operator, but never an anonymous visitor to an open demo.

    Most operator actions on a public demo are self-contained: they write
    clearly-labelled synthetic rows and nothing else. Two are not, and they are
    the ones this guards.

    * Model promotion rewrites the promotion pointer on disk and changes every
      subsequent decision for every visitor, not just the one who clicked.
    * Reconciliation spends the merchant's real Razorpay API quota.

    Both reach outside the sandbox the demo is allowed to play in, so they stay
    behind a key even while the rest of the console is open.
    """
    principal = await require_operator(x_control_plane_key)
    if principal.identity == "open-demo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this action is not available on the public demo; it requires an operator key",
        )
    return principal


async def require_admin(
    x_control_plane_key: str | None = Header(default=None),
) -> OperatorPrincipal:
    principal = await require_operator(x_control_plane_key)
    # Local demo has an explicit, documented implicit admin. In deployed
    # environments -- open demo included -- force-promotion requires a separate
    # privileged key.
    if principal.role == "admin":
        return principal
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="admin control-plane key required",
    )
