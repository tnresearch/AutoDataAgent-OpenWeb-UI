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
import json
import logging
import os
import re
import time
from typing import List, Optional
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _require_uuid(value: str, label: str = "id") -> None:
    if not _UUID_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {label} format")


# ── JWT cache (module-level singleton) ────────────────────────────────────────
_JWT_TOKEN: Optional[str] = None
_JWT_EXPIRES_AT: float = 0.0
_JWT_LOCK = asyncio.Lock()


# ── Shared httpx client ───────────────────────────────────────────────────────
# Previously every endpoint created its own AsyncClient, paying a TCP+TLS
# handshake on each call and losing keep-alive across requests. A shared
# client gives us connection pooling, a single place to tune timeouts, and
# the same retry behavior across all proxy endpoints.
_HTTPX_CLIENT: Optional[httpx.AsyncClient] = None
_HTTPX_LOCK = asyncio.Lock()

# Default request budgets. Override per-call by passing `timeout=` kwarg.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_EXPORT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=5.0)
_LOGIN_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


# Upload guardrails — prevent a single authenticated user from exhausting
# memory by submitting too many or too-large files to /analyze. These
# limits are intentionally generous for legitimate data-analysis use cases
# (the largest demo CSV is ~110KB; even Excel workbooks rarely exceed
# 50MB). Override via env if a real workload needs more.
_MAX_FILES_PER_UPLOAD = int(os.environ.get("AUTO_DATA_AGENT_MAX_FILES", "20"))
_MAX_FILE_SIZE_BYTES = int(os.environ.get("AUTO_DATA_AGENT_MAX_FILE_SIZE_MB", "100")) * 1024 * 1024
_MAX_TOTAL_UPLOAD_BYTES = int(os.environ.get("AUTO_DATA_AGENT_MAX_TOTAL_UPLOAD_MB", "300")) * 1024 * 1024


async def _get_client() -> httpx.AsyncClient:
    """Lazily create the shared httpx client and reuse it across requests."""
    global _HTTPX_CLIENT
    if _HTTPX_CLIENT is None or _HTTPX_CLIENT.is_closed:
        async with _HTTPX_LOCK:
            if _HTTPX_CLIENT is None or _HTTPX_CLIENT.is_closed:
                _HTTPX_CLIENT = httpx.AsyncClient(
                    timeout=_DEFAULT_TIMEOUT,
                    limits=httpx.Limits(
                        max_connections=50,
                        max_keepalive_connections=20,
                        # Match AutoDataAgent's hot-reload window — drops
                        # keepalive idles before the upstream uvicorn closes
                        # them, avoiding the "stale connection ECONNRESET"
                        # pattern we fixed in openai.py.
                        keepalive_expiry=5.0,
                    ),
                )
    return _HTTPX_CLIENT


def _backend_base_url() -> str:
    return os.environ.get("AUTO_DATA_AGENT_BASE_URL", "http://localhost:8003").rstrip("/")


async def _refresh_jwt() -> str:
    """Login to AutoDataAgent and cache the JWT.

    Returns the token on success. Raises HTTPException on any failure
    (missing credentials, network error, or non-200 from backend) so that
    callers can never accidentally send an empty `Authorization: Bearer `
    header — a previous bug that caused mysterious 401s.
    """
    global _JWT_TOKEN, _JWT_EXPIRES_AT
    username = os.environ.get("AUTO_DATA_AGENT_USERNAME", "")
    password = os.environ.get("AUTO_DATA_AGENT_PASSWORD", "")
    if not username or not password:
        raise HTTPException(
            status_code=500,
            detail="AutoDataAgent JWT auth requested but AUTO_DATA_AGENT_USERNAME/PASSWORD not set.",
        )

    async with _JWT_LOCK:
        # Re-check after acquiring lock: another coroutine may have refreshed.
        if _JWT_TOKEN and time.time() < _JWT_EXPIRES_AT - 60:
            return _JWT_TOKEN

        client = await _get_client()
        try:
            r = await client.post(
                f"{_backend_base_url()}/api/v1/auth/login",
                json={"username": username, "password": password},
                timeout=_LOGIN_TIMEOUT,
            )
        except httpx.RequestError as e:
            log.error(f"AutoDataAgent login network error: {e}")
            raise HTTPException(
                status_code=502, detail=f"AutoDataAgent backend unreachable during login: {e}"
            ) from e

        if r.status_code != 200:
            log.error(f"AutoDataAgent login failed: {r.status_code} {r.text}")
            raise HTTPException(status_code=502, detail="AutoDataAgent backend login failed")

        token = (r.json() or {}).get("access_token", "")
        if not token:
            raise HTTPException(
                status_code=502,
                detail="AutoDataAgent login succeeded but returned no access_token.",
            )
        _JWT_TOKEN = token
        # Refresh proactively at 25 min (real expiry is 30 min)
        _JWT_EXPIRES_AT = time.time() + 25 * 60
        return _JWT_TOKEN


async def _auth_headers() -> dict:
    """
    Return Authorization or X-API-Key header. Username/password is preferred
    because tasks created via an API key violate the AutoDataAgent backend's
    FK constraint on users.id.
    """
    if os.environ.get("AUTO_DATA_AGENT_USERNAME"):
        if not _JWT_TOKEN or time.time() >= _JWT_EXPIRES_AT - 60:
            await _refresh_jwt()
        # _refresh_jwt either raises or guarantees _JWT_TOKEN is non-empty.
        return {"Authorization": f"Bearer {_JWT_TOKEN}"}

    api_key = os.environ.get("AUTO_DATA_AGENT_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="AutoDataAgent integration is not configured. Set AUTO_DATA_AGENT_USERNAME/PASSWORD or AUTO_DATA_AGENT_API_KEY.",
        )
    return {"X-API-Key": api_key}


async def _backend_request(
    method: str,
    path: str,
    *,
    timeout: Optional[httpx.Timeout] = None,
    **kwargs,
) -> httpx.Response:
    """Make an authenticated request to the AutoDataAgent backend.

    Centralises the boilerplate:
      * builds the URL from the base
      * attaches auth headers
      * translates network errors → 502
      * propagates 4xx/5xx as HTTPException so endpoints stay 4 lines each

    Endpoints that need to STREAM (export, SSE proxy, asset HTML patching)
    open their own context manager — this helper only handles unary calls.
    """
    client = await _get_client()
    headers = await _auth_headers()
    # Allow caller to add headers without clobbering auth.
    headers.update(kwargs.pop("headers", None) or {})
    url = f"{_backend_base_url()}{path}"
    try:
        return await client.request(
            method,
            url,
            headers=headers,
            timeout=timeout or _DEFAULT_TIMEOUT,
            **kwargs,
        )
    except httpx.RequestError as e:
        # Log the full error (which includes the upstream URL) for ops,
        # but return a sanitised message to the client. ``str(e)`` from
        # httpx typically embeds the full upstream URL — fine for logs,
        # bad for non-admin browser callers.
        log.warning("AutoDataAgent backend request failed: %s %s → %s", method, path, e)
        raise HTTPException(
            status_code=502,
            detail=f"AutoDataAgent backend is unreachable ({type(e).__name__}).",
        ) from e


def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code >= 400:
        # Don't echo arbitrary backend HTML into HTTPException details.
        text = r.text[:500] if r.headers.get("content-type", "").startswith("text/") else r.text
        raise HTTPException(status_code=r.status_code, detail=text)


@router.get("/health")
async def health(user=Depends(get_verified_user)):
    """Smoke test — confirms the backend is reachable and credentials work.

    Returns only the boolean health signal and HTTP status code. The raw
    backend URL and exception detail are intentionally NOT included to
    avoid leaking internal infrastructure topology to non-admin users.
    Admins can read the underlying error from the application log.
    """
    try:
        r = await _backend_request(
            "GET", "/api/v1/analysis/tasks?page=1&page_size=1",
            timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0),
        )
        return {
            "ok": r.status_code == 200,
            "status_code": r.status_code,
        }
    except Exception as e:
        log.warning("AutoDataAgent health check failed: %s", e)
        return {"ok": False, "status_code": 502}


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

    Enforces upload guardrails (count, per-file, total) before buffering
    bytes — protects against accidental or malicious DoS by file flood.
    """
    if len(files) > _MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=413,
            detail=f"Too many files (max {_MAX_FILES_PER_UPLOAD}).",
        )

    multipart_files = []
    total_bytes = 0
    for f in files:
        content = await f.read()
        size = len(content)
        if size > _MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File '{f.filename}' is {size // (1024*1024)}MB, exceeds "
                    f"the {_MAX_FILE_SIZE_BYTES // (1024*1024)}MB per-file limit."
                ),
            )
        total_bytes += size
        if total_bytes > _MAX_TOTAL_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Combined upload exceeds the "
                    f"{_MAX_TOTAL_UPLOAD_BYTES // (1024*1024)}MB total limit."
                ),
            )
        multipart_files.append(
            ("files", (f.filename, content, f.content_type or "text/csv"))
        )

    data = {"question": question, "agent_engine": agent_engine}
    if model_id:
        data["model_id"] = model_id

    r = await _backend_request(
        "POST", "/api/v1/analysis/upload",
        data=data,
        files=multipart_files,
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=5.0),
    )
    _raise_for_status(r)
    return r.json()


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user=Depends(get_verified_user)):
    """Poll task status / metadata."""
    _require_uuid(task_id, "task_id")
    r = await _backend_request("GET", f"/api/v1/analysis/tasks/{task_id}")
    _raise_for_status(r)
    return r.json()


@router.get("/tasks/{task_id}/results")
async def get_task_results(task_id: str, user=Depends(get_verified_user)):
    """Fetch full task results (charts + insights + data_files)."""
    _require_uuid(task_id, "task_id")
    r = await _backend_request("GET", f"/api/v1/analysis/tasks/{task_id}/results")
    _raise_for_status(r)
    return r.json()


_EXPORT_FORMATS = {"pdf", "word", "pptx"}
# Headers we forward verbatim from the upstream export response so the
# browser treats the proxy result as a download.
_FORWARDED_DOWNLOAD_HEADERS = ("content-disposition", "content-type", "cache-control")


@router.get("/tasks/{task_id}/export")
async def export_task(
    task_id: str,
    format: str = "pdf",
    user=Depends(get_verified_user),
):
    """Proxy export endpoint — streams PDF/Word/PPTX report bytes.

    Streams the response body directly to the client instead of buffering
    the entire file in memory (previously a 50MB PDF held 50MB of RAM
    while the StreamingResponse drained).
    """
    _require_uuid(task_id, "task_id")
    fmt = format.lower().strip()
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"Format must be one of: {sorted(_EXPORT_FORMATS)}")

    client = await _get_client()
    auth = await _auth_headers()
    upstream = f"{_backend_base_url()}/api/v1/analysis/tasks/{task_id}/export?format={fmt}"

    # `client.stream` is a context manager — we have to keep it alive for
    # the entire StreamingResponse generator, so we drive both from inside
    # the generator and yield from there.
    async def _drain():
        try:
            async with client.stream("GET", upstream, headers=auth, timeout=_EXPORT_TIMEOUT) as r:
                if r.status_code >= 400:
                    yield (await r.aread()) or b"Export failed"
                    return
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk
        except httpx.RequestError as e:
            log.error(f"export_task stream error: {e}")
            # We've already started the response; can't send a clean HTTP
            # error code, so yield a tiny error payload so the browser at
            # least sees a non-empty file with a recognisable problem.
            yield f"\nERROR: backend disconnected: {e}\n".encode()

    # Peek at upstream headers first so we forward content-type/disposition.
    # We do a HEAD-like trick: open the stream once just to read headers,
    # but since httpx 0.27+ exposes headers before body, we can do this
    # inline. To keep things simple we just kick off the stream once and
    # rely on FastAPI's StreamingResponse to set status/headers from
    # whatever the upstream returns. We pre-fetch headers via a small probe.
    try:
        async with client.stream("GET", upstream, headers=auth, timeout=_EXPORT_TIMEOUT) as probe:
            forwarded = {
                h: probe.headers[h]
                for h in _FORWARDED_DOWNLOAD_HEADERS
                if h in probe.headers
            }
            status = probe.status_code
            media = probe.headers.get("content-type", "application/octet-stream")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}") from e

    if status >= 400:
        raise HTTPException(status_code=status, detail="Export failed")

    return StreamingResponse(
        _drain(),
        media_type=media,
        headers=forwarded,
    )


@router.post("/tasks/{task_id}/stop")
async def stop_task(task_id: str, user=Depends(get_verified_user)):
    """Cancel a running analysis task."""
    _require_uuid(task_id, "task_id")
    r = await _backend_request("POST", f"/api/v1/analysis/tasks/{task_id}/stop")
    _raise_for_status(r)
    return r.json()


@router.post("/tasks/{task_id}/rerun")
async def rerun_task(task_id: str, user=Depends(get_verified_user)):
    """Re-run a completed/failed analysis task with the same inputs."""
    _require_uuid(task_id, "task_id")
    r = await _backend_request("POST", f"/api/v1/analysis/tasks/{task_id}/rerun")
    _raise_for_status(r)
    return r.json()


@router.get("/tasks/{task_id}/execution-trace")
async def get_execution_trace(task_id: str, user=Depends(get_verified_user)):
    """Fetch step-level execution trace (used by ExecutionTracePanel)."""
    _require_uuid(task_id, "task_id")
    r = await _backend_request("GET", f"/api/v1/analysis/tasks/{task_id}/execution-trace")
    _raise_for_status(r)
    return r.json()


@router.get("/tasks/{task_id}/stream")
async def stream_task(task_id: str, user=Depends(get_verified_user)):
    """
    Server-Sent Events proxy. The browser EventSource hits us; we hold
    an upstream connection to the AutoDataAgent SSE endpoint and pass
    chunks through. This avoids CORS pain and keeps the API key server-side.
    """
    _require_uuid(task_id, "task_id")
    upstream = f"{_backend_base_url()}/api/v1/analysis/tasks/{task_id}/stream"
    client = await _get_client()
    auth = await _auth_headers()

    async def event_proxy():
        try:
            # timeout=None on the stream itself; the auth/connect budgets
            # are inherited from the client. SSE legitimately holds a
            # connection open indefinitely.
            async with client.stream(
                "GET", upstream, headers=auth, timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=5.0)
            ) as r:
                if r.status_code >= 400:
                    body = await r.aread()
                    yield f"data: {json.dumps({'error': f'upstream {r.status_code}', 'body': body.decode(errors='replace')[:500]})}\n\n".encode()
                    return
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk
        except httpx.RequestError as e:
            # Upstream dropped mid-stream — emit a final event so the
            # browser EventSource sees a clean termination instead of an
            # ambiguous hang.
            log.warning(f"stream_task: upstream disconnect: {e}")
            yield f"data: {json.dumps({'type': 'end', 'reason': 'upstream_disconnect'})}\n\n".encode()

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

    Validation is done on the URL-DECODED path so encoded traversal
    sequences (``%2e%2e``, ``%2f``, etc.) and null bytes cannot bypass
    the allowlist check.
    """
    # Repeat URL-decode until stable — defeats double-encoding tricks
    # like ``%252e%252e`` that decode to ``%2e%2e`` then to ``..``.
    decoded = path
    for _ in range(3):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt

    # Reject null bytes outright — they truncate paths in some upstreams.
    if "\x00" in decoded or "\x00" in path:
        raise HTTPException(status_code=400, detail="Invalid asset path")

    if not decoded.startswith("/"):
        decoded = "/" + decoded

    # Reject path traversal in the DECODED form, and any double-slash that
    # would create an ambiguous path. Backslash check guards against
    # Windows-style traversal that some backends mishandle.
    if (".." in decoded
            or "//" in decoded[1:]
            or "\\" in decoded):
        raise HTTPException(status_code=400, detail="Invalid asset path")
    if not any(decoded.startswith(p) for p in _ASSET_PATH_ALLOWLIST):
        raise HTTPException(status_code=400, detail="Asset path is not whitelisted")

    # Forward the DECODED, validated path so the upstream cannot re-decode
    # into something different from what we validated.
    path = decoded

    r = await _backend_request("GET", path)
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


@router.get("/insights/{insight_id}")
async def get_insight(insight_id: str, user=Depends(get_verified_user)):
    """Full insight detail (classification, evidence_refs, scores)."""
    _require_uuid(insight_id, "insight_id")
    r = await _backend_request("GET", f"/api/v1/insights/{insight_id}")
    _raise_for_status(r)
    return r.json()


@router.get("/insights/{insight_id}/charts")
async def get_insight_charts(insight_id: str, user=Depends(get_verified_user)):
    """Charts linked to a specific insight."""
    _require_uuid(insight_id, "insight_id")
    r = await _backend_request("GET", f"/api/v1/insights/{insight_id}/charts")
    _raise_for_status(r)
    return r.json()


@router.get("/memos/by-insight/{insight_id}")
async def get_memo_by_insight(insight_id: str, user=Depends(get_verified_user)):
    """The structured memo (executive_summary, key_drivers, options, etc.) attached to an insight."""
    _require_uuid(insight_id, "insight_id")
    r = await _backend_request("GET", f"/api/v1/memos/by-insight/{insight_id}")
    _raise_for_status(r)
    return r.json()


@router.get("/connections")
async def list_connections(user=Depends(get_verified_user)):
    """List all data connections (database / SaaS) registered in AutoDataAgent."""
    r = await _backend_request("GET", "/api/v1/data/connections")
    _raise_for_status(r)
    return r.json()


class _SuggestionsRequest(BaseModel):
    source_ids: List[str]
    max_suggestions: int = 5


@router.post("/suggest-questions")
async def suggest_questions(body: _SuggestionsRequest, user=Depends(get_verified_user)):
    """
    Suggest relevant analytical questions for a set of database sources.
    Wraps backend's /api/v1/analysis/suggest-questions-from-sources.
    """
    if not body.source_ids:
        raise HTTPException(status_code=400, detail="source_ids must not be empty")

    r = await _backend_request(
        "POST",
        "/api/v1/analysis/suggest-questions-from-sources",
        data={
            "source_ids": json.dumps(body.source_ids),
            "max_suggestions": str(body.max_suggestions),
        },
    )
    _raise_for_status(r)
    return r.json()


@router.get("/connections/{connection_id}/sources")
async def list_connection_sources(connection_id: str, user=Depends(get_verified_user)):
    """List data sources (tables) belonging to a connection."""
    _require_uuid(connection_id, "connection_id")
    r = await _backend_request("GET", f"/api/v1/data/connections/{connection_id}/sources")
    _raise_for_status(r)
    return r.json()


@router.get("/status")
async def integration_status(user=Depends(get_verified_user)):
    """Reports whether integration is configured (used by the model picker UI).

    Only the boolean is returned — the backend URL is intentionally
    omitted because it can reveal internal hostnames (e.g.
    ``http://auto-data-agent.svc.internal:8003``) to any logged-in user.
    """
    return {
        "enabled": bool(
            os.environ.get("AUTO_DATA_AGENT_API_KEY")
            or os.environ.get("AUTO_DATA_AGENT_USERNAME")
        ),
    }


async def close_client() -> None:
    """Release the shared httpx client. Call during app shutdown."""
    global _HTTPX_CLIENT
    if _HTTPX_CLIENT is not None and not _HTTPX_CLIENT.is_closed:
        await _HTTPX_CLIENT.aclose()
    _HTTPX_CLIENT = None
