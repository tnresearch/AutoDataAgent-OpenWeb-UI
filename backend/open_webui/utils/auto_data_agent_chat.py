"""
Chat handler for the virtual `auto-data-analyst` model.

Intercepts an Open WebUI chat completion request, extracts the user's question
and uploaded CSV files, forwards them to the AutoDataAgent_Backend service,
and streams an OpenAI-compatible response back to the frontend.

The streamed response embeds:
  - Progress messages as `delta.content` so plain Markdown clients still see
    something useful.
  - A trailing `<!--auto-data-analyst:task_id=UUID-->` marker that the
    frontend message renderer detects and replaces with the rich analysis UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Any, List, Optional

import httpx
from fastapi import HTTPException, Request
from starlette.responses import StreamingResponse

from open_webui.models.files import Files
from open_webui.storage.provider import Storage

log = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────────

ANALYSIS_MODEL_ID = "auto-data-analyst"

ANALYZABLE_EXT = {".csv", ".xlsx", ".xls", ".json", ".txt", ".pdf"}

TERMINAL_STATUSES = {"completed", "failed"}

# Upload guardrails — mirror those in routers/auto_data_agent.py so an
# `auto-data-analyst` model invocation can't accidentally bypass the
# Path-C limits by going through the chat-completions middleware.
_MAX_FILES_PER_UPLOAD = int(os.environ.get("AUTO_DATA_AGENT_MAX_FILES", "20"))
_MAX_FILE_SIZE_BYTES = int(os.environ.get("AUTO_DATA_AGENT_MAX_FILE_SIZE_MB", "100")) * 1024 * 1024
_MAX_TOTAL_UPLOAD_BYTES = int(os.environ.get("AUTO_DATA_AGENT_MAX_TOTAL_UPLOAD_MB", "300")) * 1024 * 1024

# MIME type guess by extension. The AutoDataAgent backend looks at the
# extension anyway, but a sensible Content-Type avoids surprises in any
# middleware that sniffs the multipart parts.
_EXT_MIME = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
}

# Regex used to detect a marker emitted by a prior turn.
# UUID is 8-4-4-4-12 hex; a loose [0-9a-f-]+ would greedily eat the closing
# "--" of the HTML comment and yield an invalid task_id.
_TASK_MARKER_RE = re.compile(
    r"<!--auto-data-analyst:task_id="
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"-->",
    re.I,
)

# Lenient UUID matcher — used to drop garbage source_ids before they
# hit the AutoDataAgent backend, matching the cleaning the backend does
# itself in agent_tools.analyze._clean_source_ids.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _last_task_id_in_history(messages: List[dict]) -> Optional[str]:
    """Look back through assistant messages for a previous analysis marker."""
    for m in reversed(messages or []):
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if isinstance(content, str):
            match = _TASK_MARKER_RE.search(content)
            if match:
                return match.group(1)
    return None


def _backend_url() -> str:
    return os.environ.get("AUTO_DATA_AGENT_BASE_URL", "http://localhost:8003").rstrip("/")


async def _auth_headers() -> dict:
    """Reuse the proxy router's JWT cache so we don't open two parallel sessions."""
    from open_webui.routers.auto_data_agent import _auth_headers as _proxy_auth_headers
    return await _proxy_auth_headers()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_question(form_data: dict) -> str:
    """
    Pull the user's *original* prompt out of the chat-completion payload.

    Open WebUI's RAG middleware overwrites the last user message with a
    `### Task: …  <context>…</context>` template before it reaches us.
    The frontend still ships the unmodified message in form_data['user_message'],
    so we prefer that. We fall back to scanning the messages array, but if
    the latest user message clearly contains the RAG envelope we strip it
    rather than pass the whole thing to the analysis backend.
    """
    raw = form_data.get("user_message")
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            joined = " ".join(parts).strip()
            if joined:
                return joined

    messages = form_data.get("messages") or []
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        text = text.strip()
        if not text:
            continue
        # Strip OWUI's RAG envelope if the middleware augmented the message.
        return _strip_rag_envelope(text)
    return ""


_RAG_TASK_HEADER = "### Task:"
_RAG_CONTEXT_OPEN = "<context>"
_RAG_CONTEXT_CLOSE = "</context>"


def _strip_rag_envelope(text: str) -> str:
    """
    OWUI prepends ``### Task: …  <context>…</context>`` and appends the user's
    original question after the closing tag. Recover the tail; if the pattern
    isn't fully present, return the original text unchanged.

    The previous fallback (take the last non-empty line when ``</context>``
    is missing) lost legitimate user text whose question happened to mention
    ``### Task:`` — e.g. a multi-line spec ending with the user's actual ask.
    """
    if _RAG_TASK_HEADER not in text:
        return text
    if _RAG_CONTEXT_OPEN not in text or _RAG_CONTEXT_CLOSE not in text:
        # Looks like the user just *mentioned* "### Task:" rather than the
        # OWUI middleware injecting a full envelope. Leave the text alone.
        return text
    end = text.rfind(_RAG_CONTEXT_CLOSE)
    tail = text[end + len(_RAG_CONTEXT_CLOSE):].strip()
    return tail or text


async def _extract_csv_files(form_data: dict) -> List[dict]:
    """
    Pull file references from the form_data. Open WebUI puts uploaded files
    at form_data['files'] (top-level) or form_data['metadata']['files'].
    Returns a list of {id, filename, path} dicts for files we can analyse.
    """
    candidates: List[dict] = []
    metadata = form_data.get("metadata") or {}
    candidates.extend(metadata.get("files") or [])
    candidates.extend(form_data.get("files") or [])

    seen_ids = set()
    resolved: List[dict] = []
    for entry in candidates:
        # Only handle Open WebUI's 'file' attachments (uploaded), not
        # 'collection' / 'folder' references.
        if entry.get("type") and entry["type"] not in {"file", "doc"}:
            continue

        file_obj = entry.get("file") or {}
        file_id = file_obj.get("id") or entry.get("id")
        if not file_id or file_id in seen_ids:
            continue

        # Try cached metadata from form_data first to avoid an extra DB hit
        filename = file_obj.get("filename") or entry.get("filename") or entry.get("name")
        path = file_obj.get("path") or entry.get("path")

        if not (filename and path):
            # Fall back to a DB lookup. get_file_by_id is async on the
            # Files singleton.
            record = await Files.get_file_by_id(file_id)
            if record is None:
                continue
            filename = filename or record.filename
            path = path or record.path

        ext = os.path.splitext(filename or "")[1].lower()
        if ext not in ANALYZABLE_EXT:
            continue

        seen_ids.add(file_id)
        resolved.append(
            {
                "id": file_id,
                "filename": filename,
                "path": path,
            }
        )

    return resolved


def _new_completion_id() -> str:
    """Build a unique completion ID. UUID4 avoids the collision risk of
    `int(time.time()*1000)` when multiple chunks emit in the same ms."""
    return f"chatcmpl-ada-{uuid.uuid4().hex[:24]}"


def _openai_chunk(model: str, content: str = "", finish_reason: Optional[str] = None) -> str:
    """Format an OpenAI-compatible streaming SSE chunk."""
    chunk = {
        "id": _new_completion_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _openai_full_response(model: str, content: str) -> dict:
    """Build a non-streamed OpenAI-compatible completion."""
    return {
        "id": _new_completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ── Backend interaction ────────────────────────────────────────────────────────

def _extract_source_ids(form_data: dict) -> List[str]:
    """
    Pull data source IDs from form_data. The frontend ships them via
    form_data['metadata']['data_source_ids'] (preferred) or top-level
    form_data['data_source_ids'].

    Returns a deduplicated list of lowercased, whitespace-stripped UUIDs.
    Mirrors the cleaning done by the AutoDataAgent backend
    (agent_tools.analyze._clean_source_ids) so the values we forward
    match what the backend's validator accepts. Anything that doesn't
    look like a UUID is silently dropped — this is best-effort frontend
    cleanup, the backend will still report invalid IDs explicitly.
    """
    metadata = form_data.get("metadata") or {}
    candidates = (
        metadata.get("data_source_ids")
        or form_data.get("data_source_ids")
        or []
    )
    if isinstance(candidates, str):
        # Tolerate "id1,id2" shape
        candidates = [s for s in candidates.split(",")]
    seen: set = set()
    result: List[str] = []
    for s in candidates:
        if not isinstance(s, str):
            continue
        t = s.strip().lower()
        if not t or not _UUID_RE.match(t) or t in seen:
            continue
        seen.add(t)
        result.append(t)
    return result


async def _list_org_sources() -> List[dict]:
    """Return all data sources available in the org from the AutoDataAgent backend."""
    # Reuse the proxy router's shared client so we benefit from connection
    # pooling and the keepalive_expiry tuning instead of opening a fresh
    # TCP connection per call.
    from open_webui.routers.auto_data_agent import _get_client
    try:
        client = await _get_client()
        r = await client.get(
            f"{_backend_url()}/api/v1/agent-tools/sources",
            headers=await _auth_headers(),
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        )
        if r.status_code == 200:
            return r.json().get("sources") or []
    except Exception as e:
        log.warning(f"AutoDataAgent — could not list org sources: {e}")
    return []


async def _submit_from_sources(question: str, source_ids: List[str]) -> dict:
    """POST to AutoDataAgent /api/v1/analysis/upload-from-sources for DB-source analysis."""
    from open_webui.routers.auto_data_agent import _get_client
    try:
        client = await _get_client()
        r = await client.post(
            f"{_backend_url()}/api/v1/analysis/upload-from-sources",
            headers=await _auth_headers(),
            data={
                "question": question,
                "source_ids": json.dumps(source_ids),
                "agent_engine": "openmanus",
            },
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=5.0),
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"AutoDataAgent backend unreachable: {e}") from e

    if r.status_code >= 400:
        log.error(f"AutoDataAgent upload-from-sources failed: {r.status_code} {r.text}")
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


async def _submit_to_backend(question: str, files: List[dict]) -> dict:
    """POST the question + CSV bytes to AutoDataAgent /api/v1/analysis/upload.

    Enforces upload guardrails (count / per-file / total size) before
    buffering bytes so a malformed `auto-data-analyst` invocation can't
    accidentally exhaust memory.
    """
    from open_webui.routers.auto_data_agent import _get_client

    if len(files) > _MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=413,
            detail=f"Too many files (max {_MAX_FILES_PER_UPLOAD}).",
        )

    multipart_files = []
    skipped: List[str] = []
    total_bytes = 0
    for f in files:
        if not f.get("path"):
            skipped.append(f.get("filename") or f.get("id") or "?")
            continue
        try:
            local_path = await asyncio.to_thread(Storage.get_file, f["path"])
        except Exception as e:
            log.error(f"AutoDataAgent — could not fetch {f.get('filename')}: {e}")
            skipped.append(f.get("filename") or f.get("id") or "?")
            continue

        try:
            size = await asyncio.to_thread(os.path.getsize, local_path)
        except OSError as e:
            log.error(f"AutoDataAgent — could not stat {local_path}: {e}")
            skipped.append(f.get("filename") or "?")
            continue

        if size > _MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File '{f.get('filename')}' is {size // (1024*1024)}MB, "
                    f"exceeds the {_MAX_FILE_SIZE_BYTES // (1024*1024)}MB per-file limit."
                ),
            )
        if total_bytes + size > _MAX_TOTAL_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Combined upload exceeds the "
                    f"{_MAX_TOTAL_UPLOAD_BYTES // (1024*1024)}MB total limit."
                ),
            )
        total_bytes += size

        try:
            with open(local_path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            log.error(f"AutoDataAgent — could not read {local_path}: {e}")
            skipped.append(f.get("filename") or "?")
            continue

        ext = os.path.splitext(f["filename"] or "")[1].lower()
        mime = _EXT_MIME.get(ext, "application/octet-stream")
        multipart_files.append(("files", (f["filename"], data, mime)))

    if not multipart_files:
        detail = "No readable data files were attached."
        if skipped:
            detail += f" Could not read: {', '.join(skipped)}."
        raise HTTPException(status_code=400, detail=detail)

    try:
        client = await _get_client()
        r = await client.post(
            f"{_backend_url()}/api/v1/analysis/upload",
            headers=await _auth_headers(),
            data={"question": question, "agent_engine": "openmanus"},
            files=multipart_files,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=5.0),
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"AutoDataAgent backend unreachable: {e}") from e

    if r.status_code >= 400:
        log.error(f"AutoDataAgent upload failed: {r.status_code} {r.text}")
        raise HTTPException(status_code=r.status_code, detail=r.text)

    payload = r.json()
    if skipped:
        payload["_skipped_files"] = skipped
    return payload


# ── Entry point ────────────────────────────────────────────────────────────────

async def handle_chat_completion(
    request: Request,
    form_data: dict,
    user: Any,
):
    """
    Replace the LLM call when model_id == 'auto-data-analyst'.

    Returns either a StreamingResponse (OpenAI-compatible SSE) or a plain
    JSON dict, depending on whether the client requested streaming.
    """
    streaming = bool(form_data.get("stream"))
    messages = form_data.get("messages") or []

    question = _extract_question(form_data)
    files = await _extract_csv_files(form_data)
    source_ids = _extract_source_ids(form_data)

    if not question and not files and not source_ids:
        org_sources = await _list_org_sources()
        if org_sources:
            names = ", ".join(s.get("display_name") or s.get("source_id") for s in org_sources[:5])
            extra = f" (+{len(org_sources)-5} more)" if len(org_sources) > 5 else ""
            msg = (
                "**Auto Data Analyst** is a data-analysis model.\n\n"
                f"Available database sources: **{names}{extra}**.\n\n"
                "Describe what you want to analyze and I'll run it automatically, "
                "or attach a CSV/Excel file for ad-hoc data."
            )
        else:
            msg = (
                "**Auto Data Analyst** is a data-analysis model.\n\n"
                "To analyze data, either:\n"
                "1. **Attach one or more CSV/Excel files** using the **+** button below, OR\n"
                "2. **Pick a registered database source** from the data-source picker, OR\n"
                "3. Just upload a file — I'll figure it out.\n\n"
                "Then describe what you want to analyze (e.g. *'find sales trends and regional anomalies'*)."
            )
        return _build_response(form_data, msg, streaming)

    if not files and not source_ids:
        # No explicit sources selected — try to auto-discover from org.
        org_sources = await _list_org_sources()
        if org_sources:
            source_ids = [s["source_id"] for s in org_sources]
            log.info(
                "AutoDataAgent — auto-discovered %d org sources for question: %r",
                len(source_ids), question[:80],
            )
        else:
            prior_task_id = _last_task_id_in_history(messages)
            if prior_task_id:
                msg = (
                    "**Auto Data Analyst — follow-up**\n\n"
                    "Earlier in this chat I analyzed your data — see the panel above.\n\n"
                    "To ask a follow-up question, please **re-attach the same CSV(s)** "
                    "(or pick a database source) so I can run a fresh analysis with your new question.\n\n"
                    f"<!--auto-data-analyst:task_id={prior_task_id}-->"
                )
            else:
                msg = (
                    "**Auto Data Analyst**\n\n"
                    "I received your question but no data. "
                    "Please attach one or more CSV/Excel files (**+** button) or pick a database "
                    "source from the data-source picker, then resend your question."
                )
            return _build_response(form_data, msg, streaming)

    # Submit to backend — prefer source_ids when present, fall back to file upload.
    try:
        if source_ids:
            log.info(f"AutoDataAgent — submitting source-based analysis with {len(source_ids)} sources")
            task = await _submit_from_sources(question, source_ids)
        else:
            task = await _submit_to_backend(question, files)
    except HTTPException as e:
        msg = f"**Auto Data Analyst — Error**\n\n{e.detail}"
        return _build_response(form_data, msg, streaming)
    except Exception as e:
        log.exception("submit failed")
        msg = f"**Auto Data Analyst — Error**\n\n{e}"
        return _build_response(form_data, msg, streaming)

    task_id = task.get("task_id")
    if not task_id:
        msg = "**Auto Data Analyst — Error**\n\nBackend did not return a task_id."
        return _build_response(form_data, msg, streaming)

    # Build a description for the handoff message — either filenames or source labels.
    if files:
        data_label = ", ".join(f["filename"] for f in files)
    elif source_ids:
        n = len(source_ids)
        data_label = f"{n} database source{'s' if n != 1 else ''}"
    else:
        data_label = "(none)"

    if streaming:
        return StreamingResponse(
            _stream_handoff(form_data, task_id, data_label),
            media_type="text/event-stream",
        )
    return _openai_full_response(
        form_data["model"], _handoff_message(task_id, data_label)
    )


def _handoff_message(task_id: str, data_label: str) -> str:
    """
    Markdown that goes into the assistant message body. The marker triggers
    the rich frontend renderer; the visible text is a one-line summary so
    archived chats stay readable when viewed in plain markdown.
    """
    return (
        f"_Analyzing **{data_label}** — see the panel below for live progress, charts, and insights._\n\n"
        f"<!--auto-data-analyst:task_id={task_id}-->"
    )


def _build_response(form_data: dict, content: str, streaming: bool):
    if streaming:
        return StreamingResponse(
            _stream_simple_text(form_data, content),
            media_type="text/event-stream",
        )
    return _openai_full_response(form_data["model"], content)


async def _stream_simple_text(form_data: dict, content: str):
    """Yield a single chunk + finish marker in OpenAI streaming format."""
    model = form_data["model"]
    yield _openai_chunk(model, content)
    yield _openai_chunk(model, "", finish_reason="stop")
    yield "data: [DONE]\n\n"


async def _stream_handoff(form_data: dict, task_id: str, data_label: str):
    """
    Emit the marker chunk immediately and close the stream. The frontend's
    `AnalysisResult` component polls task status and renders progress on its own.
    This avoids:
      - holding the chat-completions connection open for the full analysis
        (which can be 5+ minutes)
      - duplicating polling between server and browser
    """
    model = form_data["model"]
    yield _openai_chunk(model, _handoff_message(task_id, data_label))
    yield _openai_chunk(model, "", finish_reason="stop")
    yield "data: [DONE]\n\n"
