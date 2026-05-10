<script lang="ts">
	import {
		getExecutionTrace,
		type ExecutionTrace,
		type StepTrace,
		type StepStatus
	} from '$lib/apis/auto-data-agent';

	export let taskId: string;
	export let token: string;
	/** When `true`, the panel renders expanded by default. Otherwise the
	    user clicks the header bar to expand. */
	export let defaultExpanded: boolean = false;
	/** Auto-refresh while parent thinks the task is still running. */
	export let live: boolean = false;

	let trace: ExecutionTrace | null = null;
	let loading = false;
	let error: string | null = null;
	/** True when the backend confirmed there is no trace row for this task.
	 *  Distinct from `error` because we don't want to spam Retry on 404s
	 *  (it'll keep returning 404 — the agent simply didn't record a trace). */
	let noTrace = false;
	let expanded = defaultExpanded;
	let everLoaded = false;
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let debugOpenIds: Set<string> = new Set();

	async function fetchTrace() {
		loading = true;
		error = null;
		try {
			trace = await getExecutionTrace(token, taskId);
			everLoaded = true;
			noTrace = false;
		} catch (e: any) {
			const msg = e?.message ?? String(e);
			if (/\b404\b|not found/i.test(msg)) {
				// Treat 404 as "no trace recorded". Stop polling — repeating
				// won't help, and surface a friendly message instead of an
				// alarming error block.
				noTrace = true;
				everLoaded = true;
				error = null;
				if (pollTimer) {
					clearTimeout(pollTimer);
					pollTimer = null;
				}
			} else {
				error = msg;
			}
		} finally {
			loading = false;
		}
	}

	$: hasContent =
		!!trace && (
			(trace.task_goal && (trace.task_goal.parsed_intent || trace.task_goal.success_criteria?.length))
			|| (trace.steps && trace.steps.length > 0)
		);

	$: stepCount = trace?.steps?.length ?? 0;
	$: deviationCount = trace?.deviation_count ?? 0;

	async function toggle() {
		expanded = !expanded;
		if (expanded && !everLoaded) await fetchTrace();
	}

	// While live + expanded, poll every ~5s so the panel updates as steps
	// land. Stops once the parent flips `live` to false (task terminal),
	// or once we know there's no trace row to fetch.
	$: if (live && expanded && !noTrace) {
		schedulePoll();
	} else if (pollTimer) {
		clearTimeout(pollTimer);
		pollTimer = null;
	}

	function schedulePoll() {
		if (pollTimer) clearTimeout(pollTimer);
		pollTimer = setTimeout(async () => {
			if (!live || !expanded || noTrace) return;
			await fetchTrace();
			if (live && expanded && !noTrace) schedulePoll();
		}, 5000);
	}

	import { onDestroy } from 'svelte';
	onDestroy(() => {
		if (pollTimer) clearTimeout(pollTimer);
	});

	function statusColor(s: StepStatus): string {
		return (
			{
				ok: 'bg-green-500 dark:bg-green-600',
				deviation: 'bg-amber-400 dark:bg-amber-500',
				failed: 'bg-red-500 dark:bg-red-600',
				skipped: 'bg-gray-300 dark:bg-gray-600'
			}[s] ?? 'bg-gray-400'
		);
	}

	function statusTextColor(s: StepStatus): string {
		return (
			{
				ok: 'text-green-600 dark:text-green-400',
				deviation: 'text-amber-600 dark:text-amber-400',
				failed: 'text-red-600 dark:text-red-400',
				skipped: 'text-gray-400 dark:text-gray-500'
			}[s] ?? 'text-gray-500'
		);
	}

	function fmtTime(s: string | null | undefined): string {
		if (!s) return '';
		try {
			return new Date(s).toLocaleTimeString();
		} catch {
			return s;
		}
	}

	function fmtDuration(seconds: number): string {
		if (!seconds || seconds < 0) return '';
		if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
		if (seconds < 60) return `${seconds.toFixed(1)}s`;
		const m = Math.floor(seconds / 60);
		const s = Math.round(seconds % 60);
		return `${m}m ${s}s`;
	}

	function toggleDebug(stepId: string) {
		const next = new Set(debugOpenIds);
		if (next.has(stepId)) next.delete(stepId);
		else next.add(stepId);
		debugOpenIds = next;
	}
</script>

<section class="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden bg-white dark:bg-gray-900">
	<button
		type="button"
		class="w-full flex items-center justify-between px-4 py-2 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors text-left"
		on:click={toggle}
	>
		<div class="flex items-center gap-2 min-w-0">
			<svg
				class="w-3.5 h-3.5 text-gray-400 transition-transform flex-shrink-0"
				class:rotate-90={expanded}
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
			</svg>
			<span class="text-sm font-medium text-gray-700 dark:text-gray-200">Execution trace</span>
			{#if everLoaded}
				<span class="text-xs text-gray-400 dark:text-gray-500">
					{#if noTrace}
						not recorded
					{:else if stepCount > 0}
						{stepCount} step{stepCount === 1 ? '' : 's'}
					{:else if trace?.task_goal?.parsed_intent}
						goal only
					{:else}
						no trace
					{/if}
					{#if deviationCount > 0}
						<span class="ml-1 text-amber-500 dark:text-amber-400">· {deviationCount} deviation{deviationCount === 1 ? '' : 's'}</span>
					{/if}
				</span>
			{/if}
		</div>
		{#if loading}
			<div class="w-3 h-3 border-2 border-gray-300 border-t-violet-500 rounded-full animate-spin"></div>
		{/if}
	</button>

	{#if expanded}
		<div class="px-4 pb-4 pt-1 border-t border-gray-100 dark:border-gray-800 space-y-4">
			{#if error}
				<div class="text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded">
					Could not load trace: {error}
					<button
						type="button"
						class="ml-2 underline hover:no-underline"
						on:click={fetchTrace}>Retry</button
					>
				</div>
			{:else if !everLoaded || loading}
				<div class="text-xs text-gray-500 dark:text-gray-400 py-2">Loading trace…</div>
			{:else if noTrace || !hasContent}
				<div class="text-xs text-gray-500 dark:text-gray-400 py-2">
					No execution trace was recorded for this task. Step-level traces are emitted
					by the agent engine; quick query-style tasks skip them.
				</div>
			{:else}
				<!-- Goal card -->
				{#if trace?.task_goal && (trace.task_goal.parsed_intent || trace.task_goal.success_criteria?.length)}
					<div class="rounded-md bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-100 dark:border-indigo-800/40 p-3">
						<h5 class="text-[10px] font-semibold uppercase tracking-wide text-indigo-500 dark:text-indigo-300 mb-1.5">
							Parsed goal
						</h5>
						{#if trace.task_goal.parsed_intent}
							<p class="text-sm text-gray-800 dark:text-gray-100 mb-2">{trace.task_goal.parsed_intent}</p>
						{/if}
						{#if (trace.task_goal.constraints ?? []).length}
							<div class="text-xs mb-1">
								<span class="font-semibold text-gray-500 dark:text-gray-400 mr-1">Constraints:</span>
								{#each trace.task_goal.constraints as c}
									<span
										class="inline-block rounded-full bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-200 px-2 py-0.5 mr-1 mb-1"
										>{c}</span
									>
								{/each}
							</div>
						{/if}
						{#if (trace.task_goal.success_criteria ?? []).length}
							<div class="text-xs">
								<span class="font-semibold text-gray-500 dark:text-gray-400">Success criteria:</span>
								<ol class="mt-0.5 ml-4 list-decimal text-gray-700 dark:text-gray-200 space-y-0.5">
									{#each trace.task_goal.success_criteria as sc}
										<li>{sc}</li>
									{/each}
								</ol>
							</div>
						{/if}
						{#if trace.task_goal.data_context}
							<p class="text-xs text-gray-500 dark:text-gray-400 mt-2">
								<span class="font-semibold">Data:</span> {trace.task_goal.data_context}
							</p>
						{/if}
					</div>
				{/if}

				<!-- Step timeline -->
				{#if trace?.steps?.length}
					<ol class="relative space-y-2">
						{#each trace.steps as step, i (step.step_id || i)}
							<li class="flex gap-3">
								<!-- Status circle + connector -->
								<div class="flex flex-col items-center flex-shrink-0">
									<div
										class={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-semibold text-white ${statusColor(step.status)}`}
									>
										{step.step_index + 1}
									</div>
									{#if i < trace.steps.length - 1}
										<div class="flex-1 w-px bg-gray-200 dark:bg-gray-700 mt-0.5"></div>
									{/if}
								</div>
								<!-- Step content -->
								<div class="flex-1 min-w-0 pb-2">
									<div class="flex items-start justify-between gap-2 flex-wrap">
										<p class="text-sm font-medium text-gray-800 dark:text-gray-100 leading-tight">
											{step.title || `Step ${step.step_index + 1}`}
										</p>
										<div class="flex items-center gap-2 text-[11px] text-gray-400 dark:text-gray-500 flex-shrink-0">
											<span class={statusTextColor(step.status)}>{step.status}</span>
											{#if step.duration_seconds}
												<span>· {fmtDuration(step.duration_seconds)}</span>
											{/if}
											{#if step.tokens_used}
												<span>· {step.tokens_used.toLocaleString()} tok</span>
											{/if}
										</div>
									</div>
									{#if step.description}
										<p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{step.description}</p>
									{/if}
									{#if step.output_summary}
										<p class="text-xs text-gray-600 dark:text-gray-300 mt-1 italic">
											→ {step.output_summary}
										</p>
									{/if}
									{#if step.deviation_reason}
										<p class="text-xs text-amber-600 dark:text-amber-400 mt-1">
											⚠ {step.deviation_reason}
										</p>
									{/if}
									{#if step.replanned && (step.corrective_steps_added?.length ?? 0) > 0}
										<p class="text-[11px] text-amber-500 dark:text-amber-400 mt-0.5">
											+{step.corrective_steps_added.length} corrective step(s) added
										</p>
									{/if}
									{#if step.observation && (step.observation.what_we_expected || step.observation.what_we_got || step.observation.gap)}
										<details class="mt-1 text-xs">
											<summary class="cursor-pointer text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200">
												Observation
											</summary>
											<div class="mt-1 ml-2 space-y-0.5">
												{#if step.observation.what_we_expected}
													<p class="text-gray-500 dark:text-gray-400">
														<span class="font-semibold">Expected:</span>
														{step.observation.what_we_expected}
													</p>
												{/if}
												{#if step.observation.what_we_got}
													<p class="text-gray-500 dark:text-gray-400">
														<span class="font-semibold">Got:</span>
														{step.observation.what_we_got}
													</p>
												{/if}
												{#if step.observation.gap}
													<p class="text-amber-600 dark:text-amber-400">
														<span class="font-semibold">Gap:</span>
														{step.observation.gap}
													</p>
												{/if}
											</div>
										</details>
									{/if}
									{#if step.debug_info}
										<button
											type="button"
											class="mt-1 text-[11px] text-gray-400 dark:text-gray-500 hover:text-violet-500 dark:hover:text-violet-400"
											on:click={() => toggleDebug(step.step_id || String(i))}
										>
											{debugOpenIds.has(step.step_id || String(i)) ? 'Hide' : 'Show'} debug
										</button>
										{#if debugOpenIds.has(step.step_id || String(i))}
											<pre
												class="text-[10px] mt-1 p-2 rounded bg-gray-50 dark:bg-gray-850 text-gray-600 dark:text-gray-300 overflow-auto max-h-48"
												>{JSON.stringify(step.debug_info, null, 2)}</pre>
										{/if}
									{/if}
								</div>
							</li>
						{/each}
					</ol>
				{:else if trace?.task_goal}
					<p class="text-xs text-gray-400 dark:text-gray-500 italic">
						Step-level execution not recorded for this task.
					</p>
				{/if}
			{/if}
		</div>
	{/if}
</section>
