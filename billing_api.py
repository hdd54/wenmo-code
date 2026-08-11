"""FastAPI routes for local estimates and official provider reconciliation."""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from billing_reconciliation import (
    BillingConnectorError,
    connector_status,
    get_state,
    save_admin_key,
    sync_provider,
)
from billing_service import build_billing_stats


router = APIRouter(prefix="/api/billing", tags=["billing"])


class BillingCredentialRequest(BaseModel):
    provider: str
    admin_key: str = ""


class BillingSyncRequest(BaseModel):
    provider: str
    days: int = 30


@router.get("")
def billing_stats():
    return build_billing_stats(reconciliation_state=get_state())


@router.get("/connectors")
def billing_connectors():
    return {"connectors": connector_status()}


@router.post("/credentials")
def billing_credentials(request: BillingCredentialRequest):
    try:
        save_admin_key(request.provider, request.admin_key)
    except BillingConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "provider": request.provider,
            "configured": bool(request.admin_key.strip()), "storage": "tenant_dpapi"}


@router.post("/sync")
async def billing_sync(request: BillingSyncRequest):
    try:
        snapshot = await asyncio.to_thread(sync_provider, request.provider, request.days)
    except BillingConnectorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="供应商同步失败: %s" % exc) from exc
    return {"ok": True, "snapshot": snapshot,
            "billing": build_billing_stats(reconciliation_state=get_state())}
