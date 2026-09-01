from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.auth import OperatorPrincipal, require_operator
from app.core.locks import run_with_singleton_lock
from app.integrations.razorpay.reconciliation import reconcile_open_state

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


@router.post("/run")
async def run_reconciliation(
    limit: int = Query(default=25, ge=1, le=100),
    _: OperatorPrincipal = Depends(require_operator),
) -> dict[str, Any]:
    result = await run_with_singleton_lock(
        "razorpay-reconciliation", lambda: reconcile_open_state(limit)
    )
    return result or {"ok": False, "skipped": "reconciliation already running"}
