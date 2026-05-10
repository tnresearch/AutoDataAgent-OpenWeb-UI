<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		assetUrl,
		getInsight,
		getInsightCharts,
		getMemoByInsight,
		type ChartAsset,
		type InsightDetail,
		type InsightMemo,
		type EvidenceRef
	} from '$lib/apis/auto-data-agent';
	import { createEventDispatcher } from 'svelte';

	export let insightId: string;
	export let token: string;

	const dispatch = createEventDispatcher<{ close: void }>();

	let insight: InsightDetail | null = null;
	let memo: InsightMemo | null = null;
	let charts: ChartAsset[] = [];
	let loading = true;
	let error: string | null = null;

	const SCENARIO: Record<string, string> = {
		Growth: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
		Cost: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
		Risk: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
		Customer: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
		Operations: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
		Strategy: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
		Regulatory: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'
	};
	const ANALYSIS: Record<string, string> = {
		Descriptive: 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300',
		Diagnostic: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
		Predictive: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
		Prescriptive: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
		Benchmark: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300'
	};
	const IMPACT: Record<string, string> = {
		high: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
		medium: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
		low: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300'
	};

	function isHtmlAsset(url: string): boolean {
		return /\.html?(\?|#|$)/i.test(url);
	}

	async function load() {
		loading = true;
		error = null;
		try {
			const [d, m, c] = await Promise.all([
				getInsight(token, insightId),
				getMemoByInsight(token, insightId).catch(() => null),
				getInsightCharts(token, insightId).catch(() => ({ charts: [] }))
			]);
			insight = d;
			memo = m;
			charts = c?.charts ?? [];
		} catch (e: any) {
			error = e?.message ?? String(e);
		} finally {
			loading = false;
		}
	}

	function handleKey(e: KeyboardEvent) {
		if (e.key === 'Escape') dispatch('close');
	}

	onMount(() => {
		load();
		window.addEventListener('keydown', handleKey);
	});
	onDestroy(() => window.removeEventListener('keydown', handleKey));

	function fmtPct(v: number | null | undefined): string {
		if (v == null) return '—';
		const n = v <= 1 ? Math.round(v * 100) : Math.round(v);
		return `${n}%`;
	}

	// Combine memo evidence (per-claim) with insight charts (raw) — dedupe by url
	$: combinedEvidence = (() => {
		const fromMemo: { url: string; claim: string; visual: string }[] =
			(memo?.evidence_refs ?? []).map((r: EvidenceRef) => ({
				url: r.data_ref,
				claim: r.claim,
				visual: r.visual_type
			}));
		const seen = new Set(fromMemo.map((e) => e.url));
		const fromCharts = charts
			.filter((c) => c.png_url && !seen.has(c.png_url))
			.map((c) => ({
				url: c.png_url,
				claim: c.title || 'Chart',
				visual: c.chart_type || 'chart'
			}));
		return [...fromMemo, ...fromCharts];
	})();
</script>

<!-- Backdrop -->
<div
	class="fixed inset-0 bg-black/50 z-50"
	on:click={() => dispatch('close')}
	role="presentation"
></div>

<!-- Drawer -->
<aside
	class="fixed top-0 right-0 h-full w-full max-w-2xl bg-white dark:bg-gray-900 shadow-2xl z-50 flex flex-col border-l border-gray-200 dark:border-gray-700"
	role="dialog"
	aria-modal="true"
	aria-label="Insight detail"
>
	<!-- Header -->
	<div class="flex-shrink-0 px-5 py-3 border-b border-gray-200 dark:border-gray-700 flex items-start justify-between gap-3">
		<div class="min-w-0 flex-1">
			<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
				Insight
			</p>
			<h3 class="text-base font-semibold text-gray-900 dark:text-gray-100 leading-snug">
				{insight?.title ?? 'Loading…'}
			</h3>
		</div>
		<button
			type="button"
			class="flex-shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl"
			on:click={() => dispatch('close')}
			aria-label="Close"
		>
			✕
		</button>
	</div>

	<div class="flex-1 overflow-y-auto px-5 py-4 space-y-5">
		{#if loading}
			<div class="text-sm text-gray-500 dark:text-gray-400 py-6 text-center">Loading…</div>
		{:else if error}
			<div class="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded">
				{error}
				<button class="ml-2 underline" on:click={load}>Retry</button>
			</div>
		{:else if insight}
			<!-- Badges row -->
			<div class="flex flex-wrap items-center gap-1.5">
				<span class={`px-2 py-0.5 rounded-full text-[11px] font-medium ${IMPACT[insight.impact_level] ?? 'bg-gray-100 text-gray-600'}`}>
					{insight.impact_level} impact
				</span>
				{#if insight.decision_scenario}
					<span class={`px-2 py-0.5 rounded-full text-[11px] font-medium ${SCENARIO[insight.decision_scenario] ?? 'bg-gray-100 text-gray-600'}`}>
						{insight.decision_scenario}
					</span>
				{/if}
				{#if insight.analysis_type}
					<span class={`px-2 py-0.5 rounded-full text-[11px] font-medium ${ANALYSIS[insight.analysis_type] ?? 'bg-gray-100 text-gray-600'}`}>
						{insight.analysis_type}
					</span>
				{/if}
				<span class="px-2 py-0.5 rounded-full text-[11px] bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
					{insight.status}
				</span>
			</div>

			<!-- Key Message -->
			{#if insight.key_message}
				<section>
					<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1.5">
						Key Message
					</p>
					<p class="text-sm text-gray-800 dark:text-gray-200 leading-relaxed">
						{insight.key_message}
					</p>
				</section>
			{/if}

			<!-- Tags -->
			{#if insight.content_tags && insight.content_tags.length > 0}
				<section>
					<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1.5">
						Tags
					</p>
					<div class="flex flex-wrap gap-1">
						{#each insight.content_tags as tag}
							<span class="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
								{tag}
							</span>
						{/each}
					</div>
				</section>
			{/if}

			<!-- Confidence / Impact scores -->
			{#if insight.confidence_score != null || insight.impact_score != null}
				<section class="grid grid-cols-2 gap-4">
					{#if insight.confidence_score != null}
						<div>
							<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1">
								Confidence
							</p>
							<div class="flex items-center gap-2">
								<div class="flex-1 h-1.5 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
									<div
										class="h-full bg-blue-400 dark:bg-blue-500"
										style={`width: ${(insight.confidence_score <= 1 ? insight.confidence_score : insight.confidence_score / 100) * 100}%`}
									></div>
								</div>
								<span class="text-xs text-gray-600 dark:text-gray-300 w-10 text-right">
									{fmtPct(insight.confidence_score)}
								</span>
							</div>
						</div>
					{/if}
					{#if insight.impact_score != null}
						<div>
							<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1">
								Impact
							</p>
							<div class="flex items-center gap-2">
								<div class="flex-1 h-1.5 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
									<div
										class="h-full bg-violet-400 dark:bg-violet-500"
										style={`width: ${(insight.impact_score <= 1 ? insight.impact_score : insight.impact_score / 100) * 100}%`}
									></div>
								</div>
								<span class="text-xs text-gray-600 dark:text-gray-300 w-10 text-right">
									{fmtPct(insight.impact_score)}
								</span>
							</div>
						</div>
					{/if}
				</section>
			{/if}

			<!-- Memo: executive summary -->
			{#if memo?.executive_summary}
				<section>
					<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1.5">
						Executive Summary
					</p>
					<p class="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">
						{memo.executive_summary}
					</p>
				</section>
			{/if}

			<!-- Key Drivers -->
			{#if memo?.key_drivers?.length}
				<section>
					<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1.5">
						Key Drivers
					</p>
					<ul class="space-y-1.5">
						{#each memo.key_drivers as driver, i (i)}
							<li class="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
								<span class="flex-shrink-0 mt-0.5 inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-semibold {driver.importance === 'high' ? 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300' : driver.importance === 'medium' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'}">
									{i + 1}
								</span>
								<span>{driver.description}</span>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			<!-- Evidence (charts) -->
			{#if combinedEvidence.length > 0}
				<section>
					<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2">
						Evidence ({combinedEvidence.length})
					</p>
					<div class="space-y-3">
						{#each combinedEvidence as ev (ev.url)}
							<div class="rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700">
								<div class="bg-gray-50 dark:bg-gray-850 px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300 border-b border-gray-200 dark:border-gray-700">
									{ev.claim}
								</div>
								{#if isHtmlAsset(ev.url)}
									<iframe
										src={assetUrl(ev.url)}
										title={ev.claim}
										class="w-full h-72 border-0 bg-white"
										sandbox="allow-scripts"
									></iframe>
								{:else}
									<img
										src={assetUrl(ev.url)}
										alt={ev.claim}
										class="w-full bg-gray-50 dark:bg-gray-850"
										on:error={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')}
									/>
								{/if}
							</div>
						{/each}
					</div>
				</section>
			{/if}

			<!-- Options & Trade-offs -->
			{#if memo?.options_tradeoffs?.length}
				<section>
					<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2">
						Options & Trade-offs
					</p>
					<div class="space-y-2">
						{#each memo.options_tradeoffs as opt, i (i)}
							<div class="border border-gray-200 dark:border-gray-700 rounded-lg p-3 bg-gray-50 dark:bg-gray-850">
								<p class="text-sm font-medium text-gray-800 dark:text-gray-100 mb-1">
									Option {i + 1}: {opt.description}
								</p>
								{#if opt.expected_impact}
									<p class="text-xs text-gray-500 dark:text-gray-400">
										<span class="font-semibold">Impact:</span>
										{opt.expected_impact}
									</p>
								{/if}
								{#if opt.key_risks?.length}
									<p class="text-xs text-amber-600 dark:text-amber-400 mt-1">
										<span class="font-semibold">Risks:</span>
										{opt.key_risks.join(' · ')}
									</p>
								{/if}
								{#if opt.confidence != null}
									<p class="text-xs text-gray-400 dark:text-gray-500 mt-1">
										Confidence: {fmtPct(opt.confidence)}
									</p>
								{/if}
							</div>
						{/each}
					</div>
				</section>
			{/if}

			<!-- Scope & assumptions -->
			{#if memo?.scope_assumptions}
				<section>
					<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1.5">
						Scope & Assumptions
					</p>
					<div class="text-xs text-gray-600 dark:text-gray-400 space-y-1.5 leading-relaxed">
						{#if memo.scope_assumptions.coverage_scope}
							<p>
								<span class="font-semibold">Coverage:</span>
								{memo.scope_assumptions.coverage_scope}
							</p>
						{/if}
						{#if memo.scope_assumptions.exclusions?.length}
							<p>
								<span class="font-semibold">Exclusions:</span>
								{memo.scope_assumptions.exclusions.join(', ')}
							</p>
						{/if}
						{#if memo.scope_assumptions.assumptions?.length}
							<p>
								<span class="font-semibold">Assumptions:</span>
								{memo.scope_assumptions.assumptions.join(', ')}
							</p>
						{/if}
					</div>
				</section>
			{/if}

			{#if memo?.caveats?.length}
				<section class="border-l-2 border-amber-300 dark:border-amber-700 pl-3 bg-amber-50 dark:bg-amber-900/10 py-2 rounded-r">
					<p class="text-[10px] font-semibold uppercase tracking-wide text-amber-600 dark:text-amber-400 mb-1">
						Caveats
					</p>
					<ul class="text-xs text-gray-700 dark:text-gray-300 list-disc ml-4 space-y-0.5">
						{#each memo.caveats as c}
							<li>{c}</li>
						{/each}
					</ul>
				</section>
			{/if}
		{/if}
	</div>
</aside>
