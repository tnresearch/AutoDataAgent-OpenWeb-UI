# Auto Data Agent — Open WebUI Integration

This fork of Open WebUI ships two integration paths with the in-house
[`AutoDataAgent_Backend`](../AutoDataAgent_Backend) data-analysis service:

- **Tool Server (recommended)** — AutoDataAgent exposes an OpenAPI tool
  spec; any LLM that supports function calling decides when to invoke
  the analysis. Multi-tool workflows + conversational follow-ups work
  out of the box.
- **Virtual Model (legacy)** — A pseudo-model named *Auto Data Analyst*
  whose chat completion is intercepted server-side and forwarded to
  AutoDataAgent. Single-tool, no LLM reasoning around the result.

Both coexist; users pick by selecting the corresponding model in the
chat picker.

---

## Tool Server Architecture (recommended)

```
┌──────────┐      ┌────────────────────────┐      ┌──────────────────────────┐
│ Browser  │ ───▶ │ Open WebUI backend     │ ───▶ │ AutoDataAgent            │
│ (Svelte) │      │  (chat/completions     │      │  /api/v1/agent-tools/    │
│          │      │   forwards tool_calls) │      │    analyze   (FastAPI)   │
│          │ ◀─── │  /api/v1/auto-data-    │ ◀─── │  /api/v1/analysis/...    │
│          │      │  agent/* (proxy for    │      │   (status / charts /     │
│          │      │   AnalysisResult panel)│      │    insights / memo)      │
└──────────┘      └────────────────────────┘      └──────────────────────────┘
```

1. Admin registers the tool server in *Settings → Tools* pointing at
   `http://<ada>/api/v1/agent-tools` (Bearer auth = AutoDataAgent API key).
2. Admin creates a Workspace model on top of any function-calling LLM
   (e.g. MiniMax-M2.5, GPT-4o, Claude) with the **Auto Data Analyst**
   tool attached and `function_calling: native` in params.
3. User picks the custom model, opens the **Pick database sources**
   chip, and selects one or more sources.
4. User asks an analytical question. `Chat.svelte` injects a synthetic
   system message listing the picked source UUIDs so the LLM passes the
   right `source_ids` to the tool. The LLM emits a `tool_calls` block;
   Open WebUI POSTs `run_autodataagent_analysis` to AutoDataAgent.
5. The tool returns a `task_id` + LLM-friendly `summary` (insights with
   key messages, chart titles, impact breakdown). The LLM writes a
   1-2 sentence commentary; the rich `AnalysisResult` panel is rendered
   inline below by `MarkdownTokens.svelte` (auto-expanded for our tool).
6. Follow-up questions on the same data → LLM passes back `session_id`
   from the prior tool result; the tool dedups against recent completed
   tasks (60 min window) and returns the cached summary instantly.

### Backend (AutoDataAgent_Backend)
| File | Role |
|---|---|
| `backend/app/api/agent_tools/__init__.py` | Router + filtered `/openapi.json` (LLM only sees the analyse op, not the rest of the API). |
| `backend/app/api/agent_tools/analyze.py` | The `run_autodataagent_analysis` endpoint: enqueues task, soft-waits up to 300s, builds rich summary, dedups by session+question. |
| `backend/app/api/agent_tools/auth.py` | Accepts AutoDataAgent API keys via `Authorization: Bearer …` (LLM tool servers only support Bearer). |

### Frontend (this fork)
| File | Role |
|---|---|
| `src/lib/components/chat/Chat.svelte` | Detects models with the auto-data-agent tool; injects the source-UUID system message. |
| `src/lib/components/chat/Placeholder.svelte` | Shows the DataSourcePicker for any tool-attached model. |
| `src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte` | Diverts `run_autodataagent_analysis` tool results into the rich `AnalysisResult` panel. |
| `src/lib/components/chat/Messages/Markdown/ConsecutiveDetailsGroup.svelte` | Auto-expands tool-call groups containing AutoDataAgent. |
| (existing) `Messages/AutoDataAgent/AnalysisResult.svelte` etc. | Re-used unchanged from the virtual-model path. |

### Tool server registration (admin one-time)
Either via the **Settings → Tools** UI, or by setting
`TOOL_SERVER_CONNECTIONS` on Open WebUI startup:

```json
[{
  "url": "http://localhost:8003/api/v1/agent-tools",
  "path": "/openapi.json",
  "auth_type": "bearer",
  "key": "da_xxxxxxxxxxxxxxxxxxxx",
  "config": {"enable": true},
  "info": {
    "id": "auto-data-agent",
    "name": "Auto Data Analyst",
    "description": "Run a data analysis on registered DB sources."
  }
}]
```

---

## Virtual Model Architecture (legacy)

Older path — still supported, but the Tool Server path is preferred for
new workflows.

### Architecture

```
┌──────────┐      ┌────────────────────────┐      ┌────────────────────┐
│ Browser  │ ───▶ │ Open WebUI backend     │ ───▶ │ AutoDataAgent      │
│ (Svelte) │ ◀─── │  /api/v1/auto-data-    │ ◀─── │  /api/v1/analysis  │
│          │      │  agent/* (proxy)       │      │  (FastAPI :8003)   │
└──────────┘      │  /api/chat/completions │      └────────────────────┘
                  │  (intercept)           │
                  └────────────────────────┘
```

1. The browser POSTs `/api/chat/completions` with `model="auto-data-analyst"`
2. Open WebUI's chat router calls our intercept (`utils/chat.py` ↘
   `utils/auto_data_agent_chat.py`)
3. The intercept extracts the user question + uploaded files, forwards
   them to AutoDataAgent's `/api/v1/analysis/upload`, and immediately
   returns an OpenAI-compatible streaming response containing a marker:

       <!--auto-data-analyst:task_id=UUID-->

4. The browser's `ContentRenderer` detects the marker and mounts the
   `AnalysisResult` Svelte component, which polls the proxy for status
   and results, then renders charts (via the `/asset` proxy) and
   insight cards.

## Configuration

Set these env vars before starting the Open WebUI backend:

```bash
export AUTO_DATA_AGENT_BASE_URL="http://localhost:8003"
export AUTO_DATA_AGENT_USERNAME="admin"
export AUTO_DATA_AGENT_PASSWORD="goodluck"

# Optional fallback (NOT recommended — see "Auth notes" below):
export AUTO_DATA_AGENT_API_KEY="da_xxxxxxxxxxxxxxxxxxxx"
```

`dev-backend.sh` already sets these.

### Auth notes
- Username/password is **preferred**: AutoDataAgent stamps
  `analysis_tasks.created_by` with the resolved `user_id`, and rows
  authored by an API key violate the FK to `users.id`. Login obtains a
  JWT that is cached in the proxy module and refreshed every 25 minutes
  (real expiry is 30 minutes).
- The API key path is kept as a fallback for environments without
  username/password — it works for read-only endpoints but is rejected
  by `POST /api/v1/analysis/upload`.

### Disabling the integration
If neither env var is set, the virtual model is **not** injected into
`/api/models`. The rest of Open WebUI works normally.

## Files added / modified

### Backend (Python)

| File | Role |
|------|------|
| `backend/open_webui/routers/auto_data_agent.py` | Proxy router: health, analyze, get_task, get_results, stream, asset, stop, rerun, execution-trace. Manages JWT cache. |
| `backend/open_webui/utils/auto_data_agent_chat.py` | Chat-completion intercept handler — extracts question + files, calls upload endpoint, emits the marker. |
| `backend/open_webui/main.py` | Registers the router at `/api/v1/auto-data-agent`. |
| `backend/open_webui/utils/chat.py` | Routes `model=auto-data-analyst` to the intercept handler. |
| `backend/open_webui/utils/models.py` | Injects the virtual `auto-data-analyst` model into the model list. |

### Frontend (Svelte / TypeScript)

| File | Role |
|------|------|
| `src/lib/apis/auto-data-agent/index.ts` | Typed API client: getTask, getTaskResults, stopTask, rerunTask, assetUrl, marker helpers. |
| `src/lib/components/chat/Messages/AutoDataAgent/AnalysisResult.svelte` | Top-level container. Polls task status, renders header, progress, charts, insights, action buttons. |
| `src/lib/components/chat/Messages/AutoDataAgent/InsightCard.svelte` | One insight (title / impact / status / message). |
| `src/lib/components/chat/Messages/AutoDataAgent/ChartCard.svelte` | Lazy-loaded chart thumbnail with hover scale and skeleton placeholder. |
| `src/lib/components/chat/Messages/AutoDataAgent/Lightbox.svelte` | Fullscreen chart viewer with keyboard navigation. |
| `src/lib/components/chat/Messages/ContentRenderer.svelte` | Detects the marker and mounts `AnalysisResult` above the markdown body. |

## API surface (Open WebUI ↔ browser)

All paths are mounted under `/api/v1/auto-data-agent/`. All require an
authenticated Open WebUI session.

| Method | Path | Notes |
|--------|------|-------|
| `GET`  | `/health` | Smoke test, calls list-tasks on the backend. |
| `GET`  | `/status` | Reports whether integration is enabled. |
| `POST` | `/analyze` | Multipart upload: `question`, `files[]`, optional `model_id`, `agent_engine`. Mostly used by tests; the chat handler bypasses this. |
| `GET`  | `/tasks/{id}` | Task metadata + progress. |
| `GET`  | `/tasks/{id}/results` | Charts + insights + data files. |
| `GET`  | `/tasks/{id}/stream` | SSE proxy (currently unused — frontend polls instead). |
| `GET`  | `/tasks/{id}/execution-trace` | Step-level trace, when available. |
| `POST` | `/tasks/{id}/stop` | Cancel a running task. |
| `POST` | `/tasks/{id}/rerun` | Re-run with the same inputs. |
| `GET`  | `/asset?path=/static/charts/...` | Whitelisted proxy for chart PNGs and other static assets. |
| `GET`  | `/connections` | List database / SaaS connections registered in AutoDataAgent. |
| `GET`  | `/connections/{id}/sources` | List data sources (tables) belonging to a connection. |

Asset proxy whitelist (`_ASSET_PATH_ALLOWLIST` in `auto_data_agent.py`):
- `/static/charts/`
- `/static/images/`
- `/static/exports/`

## Marker protocol

Assistant messages emitted by the chat handler include a hidden HTML
comment:

    <!--auto-data-analyst:task_id=<uuid>-->

The frontend `ContentRenderer` extracts the task_id, mounts
`AnalysisResult`, and strips the marker from the visible markdown.
Storing the marker in chat history (instead of the full result payload)
keeps chats portable, exportable as plain markdown, and lets old chats
re-render correctly when reopened.

## Trigger logic

The chat completion handler short-circuits the LLM call when:

- `model_id == "auto-data-analyst"`, OR
- `model.owned_by == "auto-data-agent"` (defense in depth in case the id
  changes)

Behaviour matrix:

| Files attached | DB sources picked | Prior task in history | Result |
|---|---|---|---|
| Yes | — | — | Submit fresh analysis via `/upload` |
| No  | Yes | — | Submit fresh analysis via `/upload-from-sources` |
| No  | No  | Yes | "Earlier in this chat I analyzed your data; re-attach to follow up" |
| No  | No  | No  | Friendly "please attach a CSV or pick a source" message |

DB-source mode is enabled by selecting one or more sources in the
`DataSourcePicker` rendered in the model placeholder. The selection is
persisted in `localStorage` (key `ada-selected-source-ids`) and shipped
to the backend in the chat request body as `data_source_ids: string[]`.

## Debugging

```bash
# Health check (returns 200 if proxy + backend + JWT are all healthy)
curl -H "Authorization: Bearer <session-token>" \
  http://localhost:8080/api/v1/auto-data-agent/health

# Verify the virtual model appears in /api/models
curl -H "Authorization: Bearer <session-token>" \
  http://localhost:8080/api/models | jq '.data[] | select(.id == "auto-data-analyst")'

# Backend logs while a task runs
tail -f /tmp/.../bp7xq6coo.output | grep auto-data-agent
```

Common gotchas:
- **All models disappear**: external OpenAI provider is down. The
  `models.py` patch keeps the virtual model alive when other providers
  return zero results.
- **`coroutine has no attribute filename`**: the Files singleton's
  `get_file_by_id` is async; ensure callers `await` it.
- **FK violation on `created_by`**: API-key auth is being used; switch
  to `AUTO_DATA_AGENT_USERNAME` + `AUTO_DATA_AGENT_PASSWORD`.
- **Charts 401 in the browser**: `/asset` requires a logged-in session.
  Make sure the user is signed in to Open WebUI.

## Known limitations / future work

- **No streaming of LLM-generated insights** — the integration polls the
  REST API every 3 seconds. AutoDataAgent supports SSE on
  `/tasks/{id}/stream` but the front-end does not use it yet.
- **Execution trace panel is not rendered** — the data is fetched on
  demand by `getExecutionTrace` but no UI consumes it. Adding a
  collapsible "Steps" panel inside `AnalysisResult` would close this.
- **Multi-turn analysis re-uploads files** — every chat turn that
  carries an attached file triggers a fresh analysis. A smarter
  implementation would re-use the prior `data_source_ids`.
