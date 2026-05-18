<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		getTask,
		getTaskResults,
		stopTask,
		rerunTask,
		downloadReport,
		type AnalysisTask,
		type AnalysisResult as AnalysisResultData,
		type ChartAsset,
		type ExportFormat
	} from '$lib/apis/auto-data-agent';
	import InsightCard from './InsightCard.svelte';
	import ChartCard from './ChartCard.svelte';
	import Lightbox from './Lightbox.svelte';
	import ExecutionTracePanel from './ExecutionTracePanel.svelte';

	export let taskId: string;
	export let token: string;

	// Original task_id passed in from the chat content. Read-only — we
	// never write back to the export. When the user clicks Retry, the
	// new task_id is tracked in `currentTaskId` and remembered in
	// localStorage so a page reload / component re-mount continues
	// tracking the latest rerun instead of reverting to the original
	// failed task (which would silently undo the retry).
	const _originalTaskId = taskId;
	const _RERUN_STORAGE_KEY = 'ada:rerun-redirects';

	function _loadRerunRedirect(orig: string): string {
		try {
			const map = JSON.parse(localStorage.getItem(_RERUN_STORAGE_KEY) || '{}');
			// Follow chain in case user reran multiple times.
			let cur = orig;
			const seen = new Set<string>([cur]);
			while (map[cur] && !seen.has(map[cur])) {
				cur = map[cur];
				seen.add(cur);
			}
			return cur;
		} catch {
			return orig;
		}
	}
	function _saveRerunRedirect(from: string, to: string) {
		try {
			const map = JSON.parse(localStorage.getItem(_RERUN_STORAGE_KEY) || '{}');
			map[from] = to;
			localStorage.setItem(_RERUN_STORAGE_KEY, JSON.stringify(map));
		} catch {}
	}

	// `currentTaskId` is the *effective* task being polled. Initialised
	// to the latest rerun in storage (if any) so a reload picks up where
	// the user left off. We deliberately read from `currentTaskId`
	// (not the export `taskId`) everywhere inside this component so
	// parent re-renders don't snap us back to the original failed task.
	let currentTaskId: string = _loadRerunRedirect(_originalTaskId);

	let task: AnalysisTask | null = null;
	let result: AnalysisResultData | null = null;
	let loading = true;
	let error: string | null = null;
	let lightboxIndex: number | null = null;

	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let consecutiveErrors = 0;
	let aborted = false;
	const MAX_ERRORS = 8;       // give up after ~30 s of network failures
	const POLL_INTERVAL_MS = 3000;
	const MAX_TOTAL_POLLS = 1200; // ~1 hour ceiling
	let pollCount = 0;

	const STATUS_BADGE: Record<string, string> = {
		completed: 'bg-green-100 text-green-700 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-800',
		failed: 'bg-red-100 text-red-700 border-red-200 dark:bg-red-900/40 dark:text-red-300 dark:border-red-800',
		running: 'bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/40 dark:text-blue-300 dark:border-blue-800',
		pending: 'bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:border-gray-700',
		queued: 'bg-yellow-100 text-yellow-700 border-yellow-200 dark:bg-yellow-900/40 dark:text-yellow-300 dark:border-yellow-800'
	};

	const PROGRESS_CODE_LABEL: Record<string, string> = {
		INITIALIZING: 'Initializing analysis…',
		CLASSIFYING: 'Classifying question intent…',
		BUILDING_SCHEMA: 'Inspecting CSV schema…',
		GENERATING_CODE: 'Generating analysis code…',
		EXECUTING: 'Executing computations…',
		PERSISTING: 'Saving results…',
		DONE: 'Done',
		FAILED: 'Failed'
	};

	$: prettyProgressMessage = (() => {
		if (!task) return '';
		const code = task.progress_code;
		const msg = task.progress_message;
		if (msg && msg.trim()) return msg;
		if (code && PROGRESS_CODE_LABEL[code]) return PROGRESS_CODE_LABEL[code];
		return 'Working on it…';
	})();

	let priorStatus: string | null = null;

	function notifyOnCompletion(prev: string | null, next: string) {
		// Only fire when transitioning into a terminal state, only when the
		// browser is in the background, and only when the user has granted
		// permission. Be defensive — Notification can be undefined in some
		// embedded contexts (e.g. the desktop app's webview).
		if (prev === next) return;
		if (next !== 'completed' && next !== 'failed') return;
		if (typeof Notification === 'undefined') return;
		if (Notification.permission !== 'granted') return;
		if (typeof document !== 'undefined' && !document.hidden) return;
		try {
			const verb = next === 'completed' ? 'finished' : 'failed';
			new Notification(`Analysis ${verb}`, {
				body: task?.question ? `“${task.question.slice(0, 80)}”` : 'Open the chat to view results.',
				tag: `ada-${currentTaskId}`
			});
		} catch {
			/* swallow — non-critical */
		}
	}

	async function refresh() {
		if (aborted) return;
		pollCount += 1;
		try {
			task = await getTask(token, currentTaskId);
			error = null;
			consecutiveErrors = 0;
			notifyOnCompletion(priorStatus, task.status);
			priorStatus = task.status;
			if (task.status === 'completed' || task.status === 'failed') {
				try {
					result = await getTaskResults(token, currentTaskId);
				} catch (e) {
					console.warn('Auto Data Analyst — result fetch failed:', e);
				}
				return; // stop polling
			}
		} catch (e: any) {
			consecutiveErrors += 1;
			error = e?.message ?? String(e);
			if (consecutiveErrors >= MAX_ERRORS) {
				console.error('Auto Data Analyst — giving up after', consecutiveErrors, 'errors');
				return;
			}
		} finally {
			loading = false;
		}
		if (pollCount >= MAX_TOTAL_POLLS) {
			error = `Polling timed out after ${MAX_TOTAL_POLLS} attempts. Refresh the page to try again.`;
			return;
		}
		pollTimer = setTimeout(refresh, POLL_INTERVAL_MS);
	}

	function retry() {
		consecutiveErrors = 0;
		pollCount = 0;
		error = null;
		loading = true;
		refresh();
	}

	let actionPending: 'stop' | 'rerun' | 'export' | null = null;
	let exportMenuOpen = false;
	let exportError: string | null = null;
	let additionalChartsExpanded = false;
	let questionExpanded = false;
	const QUESTION_LINE_THRESHOLD = 200; // chars; longer than this gets a "more" toggle

	async function onStop() {
		if (!task || actionPending) return;
		actionPending = 'stop';
		try {
			await stopTask(token, currentTaskId);
			// Force-refresh to pick up the new (failed/cancelled) state
			refresh();
		} catch (e: any) {
			error = e?.message ?? String(e);
		} finally {
			actionPending = null;
		}
	}

	async function onRerun() {
		if (!task || actionPending) return;
		actionPending = 'rerun';
		try {
			// Always rerun the CURRENT task in the chain (could be the original
			// or a previously rerun task). Pre-fix this used `taskId` which is
			// a re-bindable export — when the parent component re-rendered the
			// Markdown tool block (e.g. user navigates away and back), the
			// export would snap back to _originalTaskId, so every retry click
			// would rerun the FIRST failure forever, never the latest attempt.
			const newTask = await rerunTask(token, currentTaskId);
			if (newTask?.task_id) {
				// Update local tracking AND persist so reload keeps the chain.
				currentTaskId = newTask.task_id;
				_saveRerunRedirect(_originalTaskId, newTask.task_id);
				task = null;
				result = null;
				loading = true;
				pollCount = 0;
				consecutiveErrors = 0;
				error = null;
				refresh();
			}
		} catch (e: any) {
			error = e?.message ?? String(e);
		} finally {
			actionPending = null;
		}
	}

	let chartContainerEl: HTMLElement;

	function jumpToChart(chartId: string) {
		// Find the corresponding chart, scroll into view, briefly highlight.
		const idx = charts.findIndex((c) => c.chart_id === chartId);
		if (idx < 0 || !chartContainerEl) return;
		const el = chartContainerEl.children[idx] as HTMLElement | undefined;
		if (!el) return;
		el.scrollIntoView({ behavior: 'smooth', block: 'center' });
		el.classList.add('ada-chart-highlight');
		setTimeout(() => el.classList.remove('ada-chart-highlight'), 1500);
	}

	function insertSuggestion(text: string) {
		// Drop the suggestion into the chat input. We can't reach across
		// component boundaries directly, so target the textarea/contenteditable
		// in the message composer and dispatch input events Svelte will pick up.
		const textarea = document.querySelector<HTMLTextAreaElement>('textarea');
		if (textarea) {
			const native = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
			native?.call(textarea, text);
			textarea.dispatchEvent(new Event('input', { bubbles: true }));
			textarea.focus();
			return;
		}
		const editable = document.querySelector<HTMLElement>('[contenteditable="true"]');
		if (editable) {
			editable.textContent = text;
			editable.dispatchEvent(new Event('input', { bubbles: true }));
			editable.focus();
		}
	}

	async function onExport(format: ExportFormat) {
		if (!task || actionPending) return;
		exportMenuOpen = false;
		actionPending = 'export';
		exportError = null;
		try {
			await downloadReport(token, currentTaskId, format);
		} catch (e: any) {
			exportError = e?.message ?? String(e);
		} finally {
			actionPending = null;
		}
	}

	onMount(() => {
		if (currentTaskId) refresh();
		// Request notification permission lazily; users only see this prompt
		// once per origin and only the first time they open an analysis.
		if (
			typeof Notification !== 'undefined' &&
			Notification.permission === 'default'
		) {
			Notification.requestPermission().catch(() => {
				/* user can deny — that's fine */
			});
		}
	});

	onDestroy(() => {
		aborted = true;
		if (pollTimer) clearTimeout(pollTimer);
	});

	$: charts = result?.charts ?? [];
	$: insights = result?.insights ?? [];
	$: progress = task?.progress_percent ?? 0;
	$: progressMsg = task?.progress_message ?? '';
	$: isRunning = task?.status === 'running' || task?.status === 'pending' || task?.status === 'queued';
	$: isInitialLoad = loading && !task;

	$: questionIsLong = (task?.question?.length ?? 0) > QUESTION_LINE_THRESHOLD;

	$: insightCounts = (() => {
		const total = insights.length;
		let high = 0, medium = 0, low = 0;
		for (const i of insights) {
			if (i.impact_level === 'high') high += 1;
			else if (i.impact_level === 'medium') medium += 1;
			else if (i.impact_level === 'low') low += 1;
		}
		return { total, high, medium, low };
	})();

	// Charts not linked to any insight — surface them in a folded "Additional charts"
	// block so users can still see them without dominating the result panel.
	$: orphanCharts = charts.filter((c) => !c.insight_id);
</script>

<div class="auto-data-analyst-result rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 overflow-hidden my-3">
	<!-- Header / Task metadata strip.
	     The question itself echoes back in a subtle quote-block above the
	     metadata: it grounds the panel in *what was asked* without dominating
	     the layout (it's already in the user message bubble above). -->
	{#if task}
		<div class="px-5 py-3 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-850 space-y-2.5">
			{#if task.question}
				<div class="flex items-start gap-2.5">
					<div class="flex-shrink-0 w-0.5 self-stretch bg-violet-300 dark:bg-violet-700 rounded-full"></div>
					<div class="min-w-0 flex-1">
						<p class="text-[10px] font-semibold uppercase tracking-wide text-violet-500 dark:text-violet-400 mb-0.5">
							Analyzing
						</p>
						<p
							class="text-xs italic text-gray-600 dark:text-gray-400 leading-relaxed whitespace-pre-line {questionIsLong && !questionExpanded
								? 'line-clamp-2'
								: ''}"
						>
							{task.question}
						</p>
						{#if questionIsLong}
							<button
								type="button"
								class="mt-0.5 text-[11px] text-violet-600 dark:text-violet-400 hover:underline"
								on:click={() => (questionExpanded = !questionExpanded)}
							>
								{questionExpanded ? 'Show less' : 'Show more'}
							</button>
						{/if}
					</div>
				</div>
			{/if}
			<div class="flex items-center gap-3 flex-wrap">
				<span
					class={`px-2.5 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap ${
						STATUS_BADGE[task.status] ?? 'bg-gray-100 text-gray-600 border-gray-200'
					}`}
				>
					{task.status}
				</span>
				<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400 flex-1 min-w-0">
					{#if task.csv_filenames?.length}
						<span><span class="font-medium">Source:</span> {task.csv_filenames.join(', ')}</span>
					{/if}
					{#if task.duration_seconds}
						<span><span class="font-medium">Duration:</span> {Math.round(task.duration_seconds)}s</span>
					{/if}
					{#if task.total_tokens}
						<span><span class="font-medium">Tokens:</span> {task.total_tokens.toLocaleString()}</span>
					{/if}
					{#if task.agent_engine}
						<span><span class="font-medium">Engine:</span> {task.agent_engine}</span>
					{/if}
				</div>
				<!-- Action buttons -->
				<div class="flex-shrink-0 flex items-center gap-2">
					{#if isRunning}
						<button
							type="button"
							class="text-xs border border-red-300 dark:border-red-700 text-red-600 dark:text-red-300 rounded px-2 py-1 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors disabled:opacity-50"
							on:click={onStop}
							disabled={actionPending !== null}
							title="Stop the running analysis"
						>
							{actionPending === 'stop' ? 'Stopping…' : 'Stop'}
						</button>
					{:else if task.status === 'completed' || task.status === 'failed'}
						{#if task.status === 'completed'}
							<!-- Export dropdown -->
							<div class="relative">
								<button
									type="button"
									class="text-xs border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 rounded px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-50 flex items-center gap-1"
									on:click={() => (exportMenuOpen = !exportMenuOpen)}
									disabled={actionPending !== null}
									title="Download report"
								>
									{actionPending === 'export' ? 'Exporting…' : 'Export'}
									<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
									</svg>
								</button>
								{#if exportMenuOpen}
									<div
										class="absolute right-0 mt-1 w-32 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-md shadow-lg z-10 overflow-hidden"
									>
										<button
											type="button"
											class="w-full text-left text-xs px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200"
											on:click={() => onExport('pdf')}
										>
											PDF
										</button>
										<button
											type="button"
											class="w-full text-left text-xs px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200"
											on:click={() => onExport('pptx')}
										>
											PowerPoint
										</button>
										<button
											type="button"
											class="w-full text-left text-xs px-3 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200"
											on:click={() => onExport('word')}
										>
											Word
										</button>
									</div>
								{/if}
							</div>
						{/if}
						<button
							type="button"
							class="text-xs border border-violet-300 dark:border-violet-700 text-violet-600 dark:text-violet-300 rounded px-2 py-1 hover:bg-violet-50 dark:hover:bg-violet-900/30 transition-colors disabled:opacity-50"
							on:click={onRerun}
							disabled={actionPending !== null}
							title="Re-run with the same inputs"
						>
							{actionPending === 'rerun' ? 'Submitting…' : 'Re-run'}
						</button>
					{/if}
				</div>
			</div>
		</div>
	{:else if isInitialLoad}
		<div class="px-5 py-4 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-850">
			<div class="h-4 w-2/3 bg-gray-200 dark:bg-gray-800 rounded animate-pulse mb-2"></div>
			<div class="h-3 w-1/3 bg-gray-200 dark:bg-gray-800 rounded animate-pulse"></div>
		</div>
	{/if}

	<div class="p-5 space-y-5">
		<!-- Failure banner (top of body for visibility).
		     Previously the failed-task error message lived at the bottom,
		     below empty chart/insight sections, so users browsing a failed
		     run saw only the panel header + empty space and assumed it was
		     still "loading". Surface failures prominently up front. -->
		{#if task?.status === 'failed'}
			<div
				class="border-2 border-red-300 dark:border-red-800 rounded-lg bg-red-50 dark:bg-red-900/30 px-4 py-3 flex items-start gap-3"
			>
				<svg
					class="w-5 h-5 flex-shrink-0 mt-0.5 text-red-500 dark:text-red-400"
					fill="none" viewBox="0 0 24 24" stroke="currentColor"
				>
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
						d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
				</svg>
				<div class="min-w-0 flex-1">
					<p class="text-sm font-semibold text-red-800 dark:text-red-200">
						{#if task.error_message?.startsWith('Telenor AI Factory edge proxy')}
							LLM upstream unavailable
						{:else if task.error_message?.startsWith('Historical failure')}
							Old failure (pre-fix)
						{:else if task.error_message?.startsWith('Historical OOM')}
							Old failure (OOM)
						{:else}
							Analysis failed
						{/if}
					</p>
					{#if task.error_message}
						<p class="mt-1 text-sm text-red-700 dark:text-red-300 whitespace-pre-wrap leading-snug">
							{task.error_message}
						</p>
					{/if}
					{#if task.error_message?.startsWith('Telenor AI Factory edge proxy')}
						<button
							class="mt-2 inline-flex items-center gap-1 text-xs border border-red-300 dark:border-red-700 rounded px-2.5 py-1 text-red-700 dark:text-red-200 hover:bg-red-100 dark:hover:bg-red-900/50 transition-colors disabled:opacity-50"
							on:click={onRerun}
							disabled={actionPending !== null}
							title="Re-run with same inputs"
						>
							{actionPending === 'rerun' ? 'Submitting…' : '↻ Retry analysis'}
						</button>
					{/if}
				</div>
			</div>
		{/if}

		<!-- Loading / progress -->
		{#if isInitialLoad || isRunning}
			<div class="border border-blue-200 dark:border-blue-800 rounded-lg bg-blue-50 dark:bg-blue-900/20 px-4 py-3">
				<div class="flex items-center gap-3 mb-2">
					<div class="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin flex-shrink-0"></div>
					<p class="text-sm font-medium text-blue-800 dark:text-blue-200">
						{prettyProgressMessage || 'Analysis in progress…'}
					</p>
				</div>
				<div class="w-full bg-blue-100 dark:bg-blue-950 rounded-full h-1.5">
					<div
						class="bg-blue-500 h-1.5 rounded-full transition-all duration-500"
						style={`width: ${Math.max(progress, isInitialLoad ? 5 : 0)}%`}
					></div>
				</div>
				<div class="flex justify-between items-center mt-1.5 text-xs text-blue-600 dark:text-blue-300">
					<span>{progress}%</span>
					{#if task?.progress_code}
						<span class="opacity-70 font-mono">{task.progress_code}</span>
					{/if}
				</div>
			</div>
		{/if}

		<!-- Error -->
		{#if error}
			<div
				class="border border-red-200 dark:border-red-800 rounded-lg bg-red-50 dark:bg-red-900/20 px-4 py-3 text-sm text-red-700 dark:text-red-300 flex items-start justify-between gap-3"
			>
				<div>
					<strong>Couldn't load analysis.</strong>
					<p class="mt-0.5 opacity-80">{error}</p>
				</div>
				<button
					class="flex-shrink-0 text-xs border border-red-300 dark:border-red-700 rounded px-2 py-1 hover:bg-red-100 dark:hover:bg-red-900/40 transition-colors"
					on:click={retry}
				>
					Retry
				</button>
			</div>
		{/if}

		<!-- Old bottom-of-panel failure block intentionally removed —
		     superseded by the prominent banner at top of body. -->

		{#if exportError}
			<div class="border border-red-200 dark:border-red-800 rounded-lg bg-red-50 dark:bg-red-900/20 px-4 py-2 text-xs text-red-700 dark:text-red-300">
				Export failed: {exportError}
			</div>
		{/if}

		<!-- Execution trace (collapsed by default; expands on click) -->
		{#if task && (isRunning || charts.length > 0 || insights.length > 0)}
			<ExecutionTracePanel taskId={currentTaskId} {token} live={isRunning} />
		{/if}

		<!-- Insights — primary section. Each insight opens a side drawer
		     with executive summary, key drivers, evidence charts (with
		     interpretation), options, and confidence. Loose charts not
		     linked to an insight are folded into "Additional charts" below. -->
		{#if insights.length > 0}
			<section>
				<div class="flex items-center justify-between mb-3 flex-wrap gap-2">
					<h4 class="text-sm font-semibold text-gray-800 dark:text-gray-200">
						Insights ({insightCounts.total})
					</h4>
					<div class="flex items-center gap-1.5 text-xs">
						{#if insightCounts.high > 0}
							<span class="px-2 py-0.5 rounded-full bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300">
								{insightCounts.high} high
							</span>
						{/if}
						{#if insightCounts.medium > 0}
							<span class="px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
								{insightCounts.medium} medium
							</span>
						{/if}
						{#if insightCounts.low > 0}
							<span class="px-2 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300">
								{insightCounts.low} low
							</span>
						{/if}
					</div>
				</div>
				<div class="space-y-2">
					{#each insights as insight (insight.insight_id)}
						<InsightCard {insight} {charts} {token} />
					{/each}
				</div>
			</section>
		{/if}

		<!-- Additional charts — only the orphan ones (not linked to any
		     insight). Folded by default to keep the panel focused. -->
		{#if orphanCharts.length > 0}
			<section>
				<button
					type="button"
					class="w-full flex items-center justify-between text-sm font-semibold text-gray-700 dark:text-gray-300 hover:text-violet-600 dark:hover:text-violet-400 transition-colors"
					on:click={() => (additionalChartsExpanded = !additionalChartsExpanded)}
				>
					<span>Additional charts ({orphanCharts.length})</span>
					<svg
						class="w-4 h-4 text-gray-400 transition-transform"
						class:rotate-90={additionalChartsExpanded}
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
					</svg>
				</button>
				{#if additionalChartsExpanded}
					<div class="grid grid-cols-2 md:grid-cols-3 gap-3 mt-3" bind:this={chartContainerEl}>
						{#each orphanCharts as chart, idx (chart.chart_id)}
							<ChartCard {chart} on:open={() => (lightboxIndex = idx)} />
						{/each}
					</div>
				{/if}
			</section>
		{/if}

		<!-- Empty state -->
		{#if task?.status === 'completed' && charts.length === 0 && insights.length === 0}
			<div class="text-center py-8 text-gray-400 dark:text-gray-500 text-sm">
				No charts or insights generated for this task.
			</div>
		{/if}

		<!-- Follow-up prompts: shown after a completed analysis to guide
		     the next conversation turn. Click a chip to drop the prompt
		     into the chat input — the user reviews + sends. -->
		{#if task?.status === 'completed' && (charts.length > 0 || insights.length > 0)}
			<section class="border-t border-gray-100 dark:border-gray-800 pt-4">
				<p class="text-xs text-gray-500 dark:text-gray-400 mb-2">Try asking:</p>
				<div class="flex flex-wrap gap-2">
					{#each ['Show the trend over time', 'Find anomalies and outliers', 'Compare top performers vs bottom', 'Break down by category', 'What changed month-over-month?'] as suggestion}
						<button
							type="button"
							class="text-xs px-3 py-1.5 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200 hover:border-violet-300 dark:hover:border-violet-700 hover:bg-violet-50 dark:hover:bg-violet-900/20 transition-colors"
							on:click={() => insertSuggestion(suggestion)}
						>
							{suggestion}
						</button>
					{/each}
				</div>
			</section>
		{/if}
	</div>
</div>

{#if lightboxIndex !== null && orphanCharts.length > 0}
	<Lightbox charts={orphanCharts} index={lightboxIndex} on:close={() => (lightboxIndex = null)} />
{/if}

<style>
	.auto-data-analyst-result :global(.line-clamp-2) {
		display: -webkit-box;
		-webkit-line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.auto-data-analyst-result :global(.ada-chart-highlight) {
		animation: ada-pulse 1.5s ease-out;
	}
	@keyframes ada-pulse {
		0%   { box-shadow: 0 0 0 0 rgba(139, 92, 246, 0.6); }
		50%  { box-shadow: 0 0 0 8px rgba(139, 92, 246, 0); }
		100% { box-shadow: 0 0 0 0 rgba(139, 92, 246, 0); }
	}
</style>
