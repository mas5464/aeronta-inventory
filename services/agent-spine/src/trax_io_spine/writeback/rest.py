"""httpx client to the eMRO Writeback REST surface (real #6, or the fake_emro harness)."""

from __future__ import annotations

import asyncio

import httpx

from trax_io_spine.contracts import WritebackRequest, WritebackResult, WritebackStatus


class RestWritebackClient:
    """Sync writeback client that drives an httpx.AsyncClient internally.

    Using AsyncClient lets the test harness wire in httpx.ASGITransport (async-only
    since httpx 0.28) without changing the sync WritebackTarget.write contract.
    """

    def __init__(self, base_url: str = "", client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()

    def write(self, req: WritebackRequest) -> WritebackResult:
        return asyncio.run(self._async_write(req))

    async def _async_write(self, req: WritebackRequest) -> WritebackResult:
        try:
            resp = await self._client.post(
                f"{self._base_url}/inventory-levels", json=req.model_dump()
            )
        except httpx.HTTPError as exc:
            return WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.FAILED, error_message=str(exc),
            )
        if resp.status_code == 200:
            body = resp.json()
            return WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.WRITTEN,
                old_values=body.get("old_values"), new_values=body.get("new_values"),
            )
        if resp.status_code == 409:
            return WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.DEFERRED_OPEN_ORDER,
            )
        return WritebackResult(
            tenant_id=req.tenant_id, pn=req.pn, location=req.location,
            status=WritebackStatus.FAILED, error_message=f"http {resp.status_code}",
        )
