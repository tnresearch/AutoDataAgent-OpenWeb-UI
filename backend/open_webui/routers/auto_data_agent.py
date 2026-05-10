"""
AutoDataAgent integration router.

Proxies requests from Open WebUI to the AutoDataAgent_Backend service,
authenticating with a single service-account configured via env vars.

Auth options (checked in order):
  1. AUTO_DATA_AGENT_USERNAME + AUTO_DATA_AGENT_PASSWORD  → login, cache JWT
  2. AUTO_DATA_AGENT_API_KEY                              → X-API-Key

Username/password is preferred because the AutoDataAgent backend stamps
analysis_tasks.created_by with the resolved user_id, and rows authored by
an API key fail the foreign-key constraint to users.

Env vars:
  AUTO_DATA_AGENT_BASE_URL   e.g. http://localhost:8003
  AUTO_DATA_AGENT_USERNAME   e.g. admin
  AUTO_DATA_AGENT_PASSWORD   e.g. goodluck
  AUTO_DATA_AGENT_API_KEY    optional fallback
"""

import asyncio
import logging
import os
import re
import time
from typing import List, Optional

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _require_uuid(value: str, label: str = "id") -> None:
    if not _UUID_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {label} format")

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


# ── JWT cache (module-level singleton) ────────────────────────────────────────
_JWT_TOKEN: Optional[str] = None
_JWT_EXPIRES_AT: float = 0.0
_JWT_LOCK = asyncio.Lock()


def _backend_base_url() -> str:
    return os.environ.get("AUTO_DATA_AGENT_BASE_URL", "http://localhost:8003").rstrip("/")


async def _refresh_jwt() -> str:
    global _JWT_TOKEN, _JWT_EXPIRES_AT
    username = os.environ.get("AUTO_DATA_AGENT_USERNAME", "")
    password = os.environ.get("AUTO_DATA_AGENT_PASSWORD", "")
    if not username or not password:
        return ""

    async with _JWT_LOCK:
        # Re-check after acquiring lock
        if _JWT_TOKEN and time.time() < _JWT_EXPIRES_AT - 60:
            return _JWT_TOKEN

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{_backend_base_url()}/api/v1/auth/login",
                json={"username": username, "password": password},
            )
        if r.status_code != 200:
            log.error(f"AutoDataAgent login failed: {r.status_code} {r.text}")
            raise HTTPException(status_code=502, detail="AutoDataAgent backend login failed")
        data = r.json()
        _JWT_TOKEN = data.get("access_token", "")
        # Default to 25 minutes; refresh proactively (real expiry is 30 min)
        _JWT_EXPIRES_AT = time.time() + 25 * 60
        return _JWT_TOKEN


async def _auth_headers() -> dict:
    """
    Return Authorization or X-API-Key header. Username/password is preferred
    because tasks created via an API key violate the AutoDataAgent backend's
    FK constraint on users.id.
    """
    if os.environ.get("AUTO_DATA_AGENT_USERNAME"):
        global _JWT_TOKEN
        if not _JWT_TOKEN or time.time() >= _JWT_EXPIRES_AT - 60:
            await _refresh_jwt()
        return {"Authorization": f"Bearer {_JWT_TOKEN}"}

    api_key = os.environ.get("AUTO_DATA_AGENT_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="AutoDataAgent integration is not configured. Set AUTO_DATA_AGENT_USERNAME/PASSWORD or AUTO_DATA_AGENT_API_KEY.",
        )
    return {"X-API-Key": api_key}


@router.get("/health")
async def health(user=Depends(get_verified_user)):
    """Smoke test — confirms the backend is reachable and the API key works."""
    base = _backend_base_url()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{base}/api/v1/analysis/tasks?page=1&page_size=1",
                headers=await _auth_headers(),
            )
        return {
            "ok": r.status_code == 200,
            "backend_url": base,
            "status_code": r.status_code,
        }
    except Exception as e:
        return {"ok": False, "backend_url": base, "error": str(e)}


@router.post("/analyze")
async def analyze(
    request: Request,
    question: str = Form(...),
    files: List[UploadFile] = File(...),
    model_id: Optional[str] = Form(None),
    agent_engine: str = Form("openmanus"),
    user=Depends(get_verified_user),
):
    """
    Upload one or more CSV files plus a question, returns the created
    analysis task. The frontend then polls/streams progress until completion.
    """
    base = _backend_base_url()

    multipart_files = []
    for f in files:
        content = await f.read()
        multipart_files.append(
            ("files", (f.filename, content, f.content_type or "text/csv"))
        )

    data = {"question": question, "agent_engine": agent_engine}
    if model_id:
        data["model_id"] = model_id

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{base}/api/v1/analysis/upload",
                headers=await _auth_headers(),
                data=data,
                files=multipart_files,
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user=Depends(get_verified_user)):
    """Poll task status / metadata."""
    _require_uuid(task_id, "task_id")
    base = _backend_base_url()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{base}/api/v1/analysis/tasks/{task_id}",
                headers=await _auth_headers(),
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/tasks/{task_id}/results")
async def get_task_results(task_id: str, user=Depends(get_verified_user)):
    """Fetch full task results (charts + insights + data_files)."""
    _require_uuid(task_id, "task_id")
    base = _backend_base_url()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{base}/api/v1/analysis/tasks/{task_id}/results",
                headers=await _auth_headers(),
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


_EXPORT_FORMATS = {"pdf", "word", "pptx"}


@router.get("/tasks/{task_id}/export")
async def export_task(
    task_id: str,
    format: str = "pdf",
    user=Depends(get_verified_user),
):
    """Proxy export endpoint — streams PDF/Word/PPTX report bytes."""
    _require_uuid(task_id, "task_id")
    fmt = format.lower().strip()
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"Format must be one of: {sorted(_EXPORT_FORMATS)}")

    base = _backend_base_url()
    upstream = f"{base}/api/v1/analysis/tasks/{task_id}/export?format={fmt}"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.get(upstream, headers=await _auth_headers())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail="Export failed")

    # Forward content + headers (file name, content type) so the browser
    # treats this as a downloadable file.
    headers = {}
    for h in ("content-disposition", "content-type", "cache-control"):
        if r.headers.get(h):
            headers[h] = r.headers[h]

    return StreamingResponse(
        iter([r.content]),
        media_type=r.headers.get("content-type", "application/octet-stream"),
        headers=headers,
    )


@router.post("/tasks/{task_id}/stop")
async def stop_task(task_id: str, user=Depends(get_verified_user)):
    """Cancel a running analysis task."""
    _require_uuid(task_id, "task_id")
    base = _backend_base_url()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{base}/api/v1/analysis/tasks/{task_id}/stop",
                headers=await _auth_headers(),
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.post("/tasks/{task_id}/rerun")
async def rerun_task(task_id: str, user=Depends(get_verified_user)):
    """Re-run a completed/failed analysis task with the same inputs."""
    _require_uuid(task_id, "task_id")
    base = _backend_base_url()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{base}/api/v1/analysis/tasks/{task_id}/rerun",
                headers=await _auth_headers(),
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/tasks/{task_id}/execution-trace")
async def get_execution_trace(task_id: str, user=Depends(get_verified_user)):
    """Fetch step-level execution trace (used by ExecutionTracePanel)."""
    _require_uuid(task_id, "task_id")
    base = _backend_base_url()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{base}/api/v1/analysis/tasks/{task_id}/execution-trace",
                headers=await _auth_headers(),
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/tasks/{task_id}/stream")
async def stream_task(task_id: str, user=Depends(get_verified_user)):
    """
    Server-Sent Events proxy. The browser EventSource hits us; we hold
    an upstream connection to the AutoDataAgent SSE endpoint and pass
    chunks through. This avoids CORS pain and keeps the API key server-side.
    """
    _require_uuid(task_id, "task_id")
    base = _backend_base_url()
    upstream = f"{base}/api/v1/analysis/tasks/{task_id}/stream"

    async def event_proxy():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", upstream, headers=await _auth_headers()
            ) as r:
                if r.status_code >= 400:
                    body = await r.aread()
                    yield f"data: {{\"error\": \"upstream {r.status_code}\", \"body\": {body!r}}}\n\n".encode()
                    return
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk

    return StreamingResponse(
        event_proxy(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Path prefixes the asset proxy is willing to forward. Keeping this strict
# avoids turning the proxy into an open-relay for arbitrary backend URLs.
_ASSET_PATH_ALLOWLIST = (
    "/static/charts/",
    "/static/images/",
    "/static/exports/",
)

# CSS + JS injected into chart HTML so the chart fills the iframe viewport.
# AutoDataAgent emits VChart-based HTML (visactor) where the chart container
# is hard-coded to 1200×600. We override that with 100%×100% and trigger a
# window resize event so the chart's built-in resize handler picks up the
# real iframe dimensions.
_CHART_RESPONSIVE_PATCH = """\
<style>
  /* Override the original 1200×600 chart container so it fills the iframe
     viewport. AutoDataAgent's chart HTML reads container.offsetWidth/Height
     at script init, so this CSS must apply before the body script runs —
     <head> placement guarantees that. */
  html, body { padding: 0 !important; background: white !important; }
  #chart-container {
    width: 100% !important;
    height: calc(100vh - 0px) !important;
    max-width: none !important;
    max-height: none !important;
    box-shadow: none !important;
    border-radius: 0 !important;
  }
</style>
"""


@router.get("/asset")
async def get_asset(path: str, user=Depends(get_verified_user)):
    """
    Proxy a chart / static asset from the AutoDataAgent backend.
    Restricted to a small set of safe prefixes to prevent SSRF.
    """
    if not path.startswith("/"):
        path = "/" + path

    # Reject path traversal and any prefix outside the allowlist
    if ".." in path or "//" in path[1:]:
        raise HTTPException(status_code=400, detail="Invalid asset path")
    if not any(path.startswith(p) for p in _ASSET_PATH_ALLOWLIST):
        raise HTTPException(status_code=400, detail="Asset path is not whitelisted")

    base = _backend_base_url()
    upstream = f"{base}{path}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(upstream, headers=await _auth_headers())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail="Asset fetch failed")

    content = r.content
    content_type = r.headers.get("content-type", "application/octet-stream")

    # Inject responsive sizing patch into chart HTML so it fills the iframe
    # instead of overflowing at the original 1200×600.
    if "text/html" in content_type and path.endswith((".html", ".htm")):
        try:
            text = content.decode("utf-8", errors="replace")
            patched = text.replace("</head>", _CHART_RESPONSIVE_PATCH + "</head>", 1)
            if patched != text:
                content = patched.encode("utf-8")
        except Exception as e:
            log.warning(f"Chart responsive patch failed: {e}")

    return StreamingResponse(
        iter([content]),
        media_type=content_type,
    )


async def _proxy_get(path: str) -> dict:
    base = _backend_base_url()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{base}{path}",
                headers=await _auth_headers(),
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/insights/{insight_id}")
async def get_insight(insight_id: str, user=Depends(get_verified_user)):
    """Full insight detail (classification, evidence_refs, scores)."""
    _require_uuid(insight_id, "insight_id")
    return await _proxy_get(f"/api/v1/insights/{insight_id}")


@router.get("/insights/{insight_id}/charts")
async def get_insight_charts(insight_id: str, user=Depends(get_verified_user)):
    """Charts linked to a specific insight."""
    _require_uuid(insight_id, "insight_id")
    return await _proxy_get(f"/api/v1/insights/{insight_id}/charts")


@router.get("/memos/by-insight/{insight_id}")
async def get_memo_by_insight(insight_id: str, user=Depends(get_verified_user)):
    """The structured memo (executive_summary, key_drivers, options, etc.) attached to an insight."""
    _require_uuid(insight_id, "insight_id")
    return await _proxy_get(f"/api/v1/memos/by-insight/{insight_id}")


@router.get("/connections")
async def list_connections(user=Depends(get_verified_user)):
    """List all data connections (database / SaaS) registered in AutoDataAgent."""
    base = _backend_base_url()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{base}/api/v1/data/connections",
                headers=await _auth_headers(),
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


class _SuggestionsRequest(__import__("pydantic").BaseModel):
    source_ids: list[str]
    max_suggestions: int = 5


@router.post("/suggest-questions")
async def suggest_questions(body: _SuggestionsRequest, user=Depends(get_verified_user)):
    """
    Suggest relevant analytical questions for a set of database sources.
    Wraps backend's /api/v1/analysis/suggest-questions-from-sources.
    """
    if not body.source_ids:
        raise HTTPException(status_code=400, detail="source_ids must not be empty")

    base = _backend_base_url()
    import json as _json
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{base}/api/v1/analysis/suggest-questions-from-sources",
                headers=await _auth_headers(),
                data={
                    "source_ids": _json.dumps(body.source_ids),
                    "max_suggestions": str(body.max_suggestions),
                },
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/connections/{connection_id}/sources")
async def list_connection_sources(connection_id: str, user=Depends(get_verified_user)):
    """List data sources (tables) belonging to a connection."""
    _require_uuid(connection_id, "connection_id")
    base = _backend_base_url()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{base}/api/v1/data/connections/{connection_id}/sources",
                headers=await _auth_headers(),
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


@router.get("/status")
async def integration_status(user=Depends(get_verified_user)):
    """Reports whether integration is configured (used by the model picker UI)."""
    return {
        "enabled": bool(
            os.environ.get("AUTO_DATA_AGENT_API_KEY")
            or os.environ.get("AUTO_DATA_AGENT_USERNAME")
        ),
        "backend_url": os.environ.get("AUTO_DATA_AGENT_BASE_URL", "http://localhost:8003"),
    }
