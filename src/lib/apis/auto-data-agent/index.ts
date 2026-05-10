/**
 * AutoDataAgent integration API client.
 *
 * All requests hit the Open WebUI backend, which proxies to the
 * AutoDataAgent_Backend service using a server-side service-account API key.
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';

// ── Types ─────────────────────────────────────────────────────────────────────

export type AnalysisStatus = 'pending' | 'queued' | 'running' | 'completed' | 'failed';

export interface AnalysisTask {
	task_id: string;
	status: AnalysisStatus;
	question: string;
	csv_filenames: string[];
	run_id?: string;
	model_id?: string | null;
	error_message?: string | null;
	insight_ids?: string[];
	chart_count?: number | null;
	starred?: boolean;
	created_at: string;
	updated_at: string;
	completed_at?: string | null;
	progress_percent?: number | null;
	progress_message?: string | null;
	progress_code?: string | null;
	duration_seconds?: number | null;
	input_tokens?: number | null;
	output_tokens?: number | null;
	total_tokens?: number | null;
	data_input_type?: 'csv' | 'connection' | 'mixed' | null;
	agent_engine?: string | null;
}

export interface ChartAsset {
	chart_id: string;
	insight_id?: string | null;
	title: string;
	chart_type?: string | null;
	png_url: string;
	chart_config?: Record<string, any> | null;
	interpretation?: string | null;
	sort_order: number;
}

export type ImpactLevel = 'low' | 'medium' | 'high';

export interface InsightSummary {
	insight_id: string;
	title: string;
	status: string;
	impact_level: ImpactLevel;
	key_message?: string;
}

export interface DataFile {
	file_id: string;
	filename: string;
	row_count?: number | null;
	column_count?: number | null;
	created_at?: string;
}

export interface AnalysisResult {
	task: AnalysisTask;
	data_files: DataFile[];
	charts: ChartAsset[];
	insights: InsightSummary[];
}

// ── Insight detail + memo ─────────────────────────────────────────────────────

export interface EvidenceRef {
	visual_type: string;
	claim: string;
	data_ref: string;
}

export interface KeyDriver {
	description: string;
	direction?: string;
	importance?: string;
}

export interface OptionTradeoff {
	description: string;
	expected_impact?: string;
	key_risks?: string[];
	confidence?: number;
}

export interface InsightDetail {
	insight_id: string;
	title: string;
	status: string;
	impact_level: string;
	key_message?: string | null;
	context?: Record<string, any> | null;
	evidence_refs?: EvidenceRef[] | null;
	decision_scenario?: string | null;
	analysis_type?: string | null;
	content_tags?: string[] | null;
	confidence_score?: number | null;
	impact_score?: number | null;
	classification_rationale?: string | null;
	created_at: string;
	updated_at: string;
}

export interface InsightMemo {
	memo_id: string;
	insight_id: string;
	executive_summary: string;
	key_drivers: KeyDriver[];
	evidence_refs: EvidenceRef[];
	scope_assumptions?: {
		coverage_scope?: string;
		exclusions?: string[];
		assumptions?: string[];
	};
	confidence_level?: number;
	caveats?: string[];
	options_tradeoffs?: OptionTradeoff[];
	created_at: string;
	updated_at: string;
}

// ── Execution trace ───────────────────────────────────────────────────────────

export interface TaskGoal {
	parsed_intent: string;
	constraints: string[];
	success_criteria: string[];
	data_context?: string | null;
	generated_at?: string | null;
	tokens_used?: number;
}

export type StepStatus = 'ok' | 'deviation' | 'skipped' | 'failed';

export interface StepObservation {
	what_we_expected?: string;
	what_we_got?: string;
	gap?: string;
	[k: string]: any;
}

export interface StepTrace {
	step_id: string;
	step_index: number;
	total_steps: number;
	title: string;
	description: string;
	expected_output?: string | null;
	status: StepStatus;
	started_at?: string | null;
	completed_at?: string | null;
	duration_seconds: number;
	tokens_used: number;
	output_summary?: string;
	observation?: StepObservation | null;
	deviation_reason?: string | null;
	replanned?: boolean;
	corrective_steps_added?: any[];
	react_supplement?: string | null;
	debug_info?: Record<string, any> | null;
}

export interface ExecutionTrace {
	task_id: string;
	task_goal?: TaskGoal | null;
	steps: StepTrace[];
	total_steps: number;
	deviation_count: number;
	created_at: string;
	updated_at: string;
}

// ── Data connections / sources ────────────────────────────────────────────────

export interface DataConnection {
	connection_id: string;
	name: string;
	description?: string | null;
	connection_type: string;
	is_active: boolean;
	test_status: string;
	is_starred: boolean;
	source_count: number;
}

export interface DataSource {
	source_id: string;
	connection_id: string;
	source_name: string;
	display_name?: string | null;
	row_count?: number | null;
	description?: string | null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const BASE = `${WEBUI_API_BASE_URL}/auto-data-agent`;

function authHeaders(token: string): Record<string, string> {
	return token ? { authorization: `Bearer ${token}` } : {};
}

async function jsonOrThrow(res: Response): Promise<any> {
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			detail = body.detail || body.error?.message || detail;
		} catch {
			// ignore
		}
		throw new Error(`${res.status} ${detail}`);
	}
	return res.json();
}

// ── Endpoints ─────────────────────────────────────────────────────────────────

export const integrationStatus = async (token: string): Promise<{ enabled: boolean; backend_url: string }> => {
	const res = await fetch(`${BASE}/status`, { headers: authHeaders(token) });
	return jsonOrThrow(res);
};

export const integrationHealth = async (token: string) => {
	const res = await fetch(`${BASE}/health`, { headers: authHeaders(token) });
	return jsonOrThrow(res);
};

export const getTask = async (token: string, taskId: string): Promise<AnalysisTask> => {
	const res = await fetch(`${BASE}/tasks/${taskId}`, { headers: authHeaders(token) });
	return jsonOrThrow(res);
};

export const getTaskResults = async (token: string, taskId: string): Promise<AnalysisResult> => {
	const res = await fetch(`${BASE}/tasks/${taskId}/results`, { headers: authHeaders(token) });
	return jsonOrThrow(res);
};

export const getExecutionTrace = async (
	token: string,
	taskId: string
): Promise<ExecutionTrace> => {
	const res = await fetch(`${BASE}/tasks/${taskId}/execution-trace`, {
		headers: authHeaders(token)
	});
	return jsonOrThrow(res);
};

export const getInsight = async (token: string, insightId: string): Promise<InsightDetail> => {
	const res = await fetch(`${BASE}/insights/${insightId}`, { headers: authHeaders(token) });
	return jsonOrThrow(res);
};

export const getInsightCharts = async (
	token: string,
	insightId: string
): Promise<{ charts: ChartAsset[] }> => {
	const res = await fetch(`${BASE}/insights/${insightId}/charts`, {
		headers: authHeaders(token)
	});
	return jsonOrThrow(res);
};

export const getMemoByInsight = async (
	token: string,
	insightId: string
): Promise<InsightMemo | null> => {
	const res = await fetch(`${BASE}/memos/by-insight/${insightId}`, {
		headers: authHeaders(token)
	});
	if (res.status === 404) return null;
	return jsonOrThrow(res);
};

export const stopTask = async (token: string, taskId: string) => {
	const res = await fetch(`${BASE}/tasks/${taskId}/stop`, {
		method: 'POST',
		headers: authHeaders(token)
	});
	return jsonOrThrow(res);
};

export const rerunTask = async (token: string, taskId: string): Promise<AnalysisTask> => {
	const res = await fetch(`${BASE}/tasks/${taskId}/rerun`, {
		method: 'POST',
		headers: authHeaders(token)
	});
	return jsonOrThrow(res);
};

export const listConnections = async (
	token: string
): Promise<{ connections: DataConnection[]; total: number }> => {
	const res = await fetch(`${BASE}/connections`, { headers: authHeaders(token) });
	return jsonOrThrow(res);
};

export const listSources = async (
	token: string,
	connectionId: string
): Promise<DataSource[]> => {
	const res = await fetch(`${BASE}/connections/${connectionId}/sources`, {
		headers: authHeaders(token)
	});
	return jsonOrThrow(res);
};

export interface QuestionSuggestion {
	question: string;
	category?: string | null;
	intent?: string | null;
	analysis_type?: string | null;
	uses_columns?: string[] | null;
}

export const suggestQuestionsFromSources = async (
	token: string,
	sourceIds: string[],
	maxSuggestions: number = 5
): Promise<{ questions: QuestionSuggestion[] }> => {
	const res = await fetch(`${BASE}/suggest-questions`, {
		method: 'POST',
		headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
		body: JSON.stringify({ source_ids: sourceIds, max_suggestions: maxSuggestions })
	});
	return jsonOrThrow(res);
};

export type ExportFormat = 'pdf' | 'word' | 'pptx';

/**
 * Trigger a download of a task report.
 * Opens the URL with the auth header so the browser saves the bytes.
 */
export const downloadReport = async (
	token: string,
	taskId: string,
	format: ExportFormat = 'pdf'
): Promise<void> => {
	const res = await fetch(`${BASE}/tasks/${taskId}/export?format=${format}`, {
		headers: authHeaders(token)
	});
	if (!res.ok) {
		throw new Error(`Export failed: ${res.status}`);
	}
	// Resolve filename from content-disposition or fall back
	const cd = res.headers.get('content-disposition') || '';
	const m = cd.match(/filename="?([^"]+)"?/i);
	const fname = m?.[1] ?? `report_${taskId}.${format === 'word' ? 'docx' : format}`;

	const blob = await res.blob();
	const url = URL.createObjectURL(blob);
	const a = document.createElement('a');
	a.href = url;
	a.download = fname;
	document.body.appendChild(a);
	a.click();
	a.remove();
	URL.revokeObjectURL(url);
};

/**
 * Resolve a backend png_url (e.g. "/static/charts/abc.png") into an
 * Open WebUI URL that proxies the asset with the service-account key.
 */
export const assetUrl = (pngUrl: string): string => {
	const cleaned = pngUrl.startsWith('/') ? pngUrl : `/${pngUrl}`;
	return `${BASE}/asset?path=${encodeURIComponent(cleaned)}`;
};

/**
 * Detect the embedded marker emitted by the backend completion handler.
 * Returns the task_id if present, else null.
 *
 * NOTE: the inner pattern matches a UUID (8-4-4-4-12 hex) explicitly. A
 * looser `[0-9a-f-]+` would greedily eat the `--` of the closing `-->`
 * and produce an invalid task id with two trailing dashes.
 */
const TASK_MARKER_PATTERN =
	/<!--auto-data-analyst:task_id=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-->/i;
const TASK_MARKER_PATTERN_GLOBAL =
	/<!--auto-data-analyst:task_id=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-->/gi;

export const extractTaskMarker = (content: string): string | null => {
	if (!content) return null;
	const m = content.match(TASK_MARKER_PATTERN);
	return m ? m[1] : null;
};

/**
 * Strip the marker comment (frontend uses the structured renderer instead).
 */
export const stripTaskMarker = (content: string): string => {
	return content.replace(TASK_MARKER_PATTERN_GLOBAL, '').trim();
};

/**
 * Pull task_id out of the JSON body of a `<details type="tool_calls">` block
 * for our analysis tool. The backend serialises AnalyzeResponse as a JSON
 * blob; we just need the task_id field to feed AnalysisResult.
 *
 * Returns null when:
 *   - the result text is empty (tool still executing)
 *   - the JSON doesn't parse (chunked / truncated output)
 *   - task_id is missing or malformed
 */
export const extractAdaTaskIdFromToolResult = (resultText: string): string | null => {
	if (!resultText) return null;
	let jsonText = resultText.trim();
	// OWUI sometimes double-encodes (JSON.stringify of a JSON string)
	for (let i = 0; i < 2; i += 1) {
		try {
			const parsed = JSON.parse(jsonText);
			if (parsed && typeof parsed === 'object' && typeof parsed.task_id === 'string') {
				const m = parsed.task_id.match(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
				return m ? parsed.task_id : null;
			}
			if (typeof parsed === 'string') {
				jsonText = parsed; // peel one quote layer and retry
				continue;
			}
			return null;
		} catch {
			return null;
		}
	}
	return null;
};
