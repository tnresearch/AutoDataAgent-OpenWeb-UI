<script lang="ts">
	import { onMount } from 'svelte';
	import {
		assetUrl,
		getInsight,
		getInsightCharts,
		getMemoByInsight,
		type ChartAsset,
		type InsightSummary,
		type InsightDetail,
		type InsightMemo,
		type EvidenceRef,
		type KeyDriver
	} from '$lib/apis/auto-data-agent';
	import { slide } from 'svelte/transition';
	import Lightbox from './Lightbox.svelte';

	export let insight: InsightSummary;
	export let charts: ChartAsset[] = [];
	export let token: string = '';

	let expanded = false;
	let detailLoaded = false;
	let detailLoading = false;
	let detailError: string | null = null;

	let detail: InsightDetail | null = null;
	let memo: InsightMemo | null = null;
	let extraCharts: ChartAsset[] = [];

	// ── Color maps (mirrors Demo UI) ──────────────────────────────────────────
	const IMPACT: Record<string, string> = {
		high: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
		medium: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
		low: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300'
	};
	const STATUS: Record<string, string> = {
		new: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300',
		active: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
		reviewed: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
		approved: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
		archived: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'
	};
	const SCENARIO: Record<string, string> = {
		Growth: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
		Cost: 'bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300',
		Risk: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
		Customer: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',
		Operations: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300',
		Strategy: 'bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300',
		Regulatory: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300'
	};
	const ANALYSIS: Record<string, string> = {
		Descriptive: 'bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300',
		Diagnostic: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
		Predictive: 'bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300',
		Prescriptive: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300',
		Benchmark: 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-300'
	};

	function isHtmlAsset(url: string): boolean {
		return /\.html?(\?|#|$)/i.test(url);
	}

	function pctOf(v: number | null | undefined): number {
		if (v == null) return 0;
		return v <= 1 ? Math.round(v * 100) : Math.round(v);
	}

	function fmtPct(v: number | null | undefined): string {
		if (v == null) return '—';
		return `${pctOf(v)}%`;
	}

	function confidenceColor(pct: number): string {
		if (pct >= 80) return 'bg-green-500 dark:bg-green-600';
		if (pct >= 50) return 'bg-amber-500 dark:bg-amber-600';
		return 'bg-red-500 dark:bg-red-600';
	}

	function driverArrow(d: KeyDriver): { sym: string; cls: string } {
		const dir = (d.direction || '').toLowerCase();
		if (dir === 'up' || dir === 'positive') return { sym: '↑', cls: 'text-green-600 dark:text-green-400' };
		if (dir === 'down' || dir === 'negative') return { sym: '↓', cls: 'text-red-600 dark:text-red-400' };
		return { sym: '→', cls: 'text-gray-500 dark:text-gray-400' };
	}

	async function loadDetail() {
		if (detailLoaded || detailLoading || !token) return;
		detailLoading = true;
		detailError = null;
		try {
			const [d, m, c] = await Promise.all([
				getInsight(token, insight.insight_id),
				getMemoByInsight(token, insight.insight_id).catch(() => null),
				getInsightCharts(token, insight.insight_id).catch(() => ({ charts: [] }))
			]);
			detail = d;
			memo = m;
			extraCharts = c?.charts ?? [];
			detailLoaded = true;
		} catch (e: any) {
			detailError = e?.message ?? String(e);
		} finally {
			detailLoading = false;
		}
	}

	// Mount: eagerly fetch the lightweight insight + memo so the collapsed card
	// is already information-rich (executive summary, confidence bar, top
	// drivers). The chart fetch is part of the same Promise.all and is cheap.
	onMount(() => {
		loadDetail();
	});

	function toggle() {
		expanded = !expanded;
	}

	$: combinedEvidence = (() => {
		const fromMemo: { url: string; claim: string; visual: string }[] = (
			memo?.evidence_refs ?? []
		).map((r: EvidenceRef) => ({
			url: r.data_ref,
			claim: r.claim,
			visual: r.visual_type
		}));
		const seen = new Set(fromMemo.map((e) => e.url));
		const fromCharts = extraCharts
			.filter((c) => c.png_url && !seen.has(c.png_url))
			.map((c) => ({
				url: c.png_url,
				claim: c.title || 'Chart',
				visual: c.chart_type || 'chart'
			}));
		return [...fromMemo, ...fromCharts];
	})();

	// Adapt combinedEvidence to the ChartAsset shape expected by Lightbox so
	// the user can click any chart and browse all evidence in fullscreen.
	$: lightboxCharts = combinedEvidence.map((ev, i) => ({
		chart_id: `${insight.insight_id}-${i}`,
		title: ev.claim,
		png_url: ev.url,
		chart_type: ev.visual,
		sort_order: i
	})) as ChartAsset[];

	let lightboxIndex: number | null = null;

	$: previewSummary = memo?.executive_summary || insight.key_message || '';
	$: confidencePct = pctOf(memo?.confidence_level ?? detail?.confidence_score);
	$: hasConfidence = (memo?.confidence_level ?? detail?.confidence_score) != null;
	$: chartCount = (detail?.context as any)?.chart_count ?? null;
</script>

<article class="border border-gray-200 dark:border-gray-700 rounded-xl bg-white dark:bg-gray-900 shadow-sm hover:shadow-md transition-shadow overflow-hidden">
	<!-- ── Title + badges ────────────────────────────────────────────── -->
	<div class="px-4 pt-3.5 pb-2.5">
		<div class="flex items-start gap-2 mb-2">
			<svg class="w-4 h-4 text-amber-500 dark:text-amber-400 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
			</svg>
			<h4 class="text-sm font-semibold text-gray-900 dark:text-gray-100 leading-snug flex-1 break-words">
				{insight.title}
			</h4>
		</div>

		<div class="flex flex-wrap items-center gap-1.5 ml-6">
			<span class={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wide ${IMPACT[insight.impact_level] ?? 'bg-gray-100 text-gray-600'}`}>
				{insight.impact_level}
			</span>
			<span class={`text-[11px] px-2 py-0.5 rounded-full ${STATUS[insight.status] ?? 'bg-gray-100 text-gray-500'}`}>
				{insight.status}
			</span>
			{#if chartCount != null && chartCount > 0}
				<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300">
					<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
					</svg>
					{chartCount} charts
				</span>
			{/if}
			{#if detail?.decision_scenario}
				<span class={`text-[11px] font-medium px-2 py-0.5 rounded-full ${SCENARIO[detail.decision_scenario] ?? 'bg-gray-100 text-gray-600'}`}>
					{detail.decision_scenario}
				</span>
			{/if}
			{#if detail?.analysis_type}
				<span class={`text-[11px] font-medium px-2 py-0.5 rounded-full ${ANALYSIS[detail.analysis_type] ?? 'bg-gray-100 text-gray-600'}`}>
					{detail.analysis_type}
				</span>
			{/if}
		</div>
	</div>

	<!-- ── Executive summary (full text, with explicit label) ────────── -->
	{#if previewSummary}
		<div class="px-4 pb-2.5 ml-6">
			<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">
				{memo?.executive_summary ? 'Executive Summary' : 'Key Message'}
			</p>
			<p class="text-sm text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-line">
				{previewSummary}
			</p>
		</div>
	{:else if detailLoading && !insight.key_message}
		<div class="px-4 pb-2 ml-6 space-y-1.5">
			<div class="h-3 w-20 bg-gray-100 dark:bg-gray-800 rounded animate-pulse"></div>
			<div class="h-3 w-full bg-gray-100 dark:bg-gray-800 rounded animate-pulse"></div>
			<div class="h-3 w-5/6 bg-gray-100 dark:bg-gray-800 rounded animate-pulse"></div>
		</div>
	{/if}

	<!-- ── Confidence bar ─────────────────────────────────────────── -->
	{#if hasConfidence}
		<div class="px-4 pb-2 ml-6">
			<div class="flex items-center justify-between mb-0.5">
				<span class="text-[11px] text-gray-500 dark:text-gray-400 font-medium">Confidence</span>
				<span class="text-[11px] font-bold text-gray-700 dark:text-gray-200">{confidencePct}%</span>
			</div>
			<div class="h-1.5 bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
				<div
					class={`h-full rounded-full transition-all duration-300 ${confidenceColor(confidencePct)}`}
					style={`width: ${confidencePct}%`}
				></div>
			</div>
		</div>
	{/if}

	<!-- ── Footer toggle action ─────────────────────────────────────── -->
	<button
		type="button"
		class="w-full px-4 py-2 border-t border-gray-100 dark:border-gray-800 bg-gray-50/70 dark:bg-gray-850/40 text-left flex items-center gap-1.5 text-xs font-medium text-violet-600 dark:text-violet-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
		on:click={toggle}
		aria-expanded={expanded}
	>
		<span>{expanded ? 'Hide details' : 'Show full memo'}</span>
		<svg
			class="w-3.5 h-3.5 transition-transform"
			class:rotate-180={expanded}
			fill="none"
			stroke="currentColor"
			viewBox="0 0 24 24"
		>
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
		</svg>
	</button>

	<!-- ── Expanded detail panel ────────────────────────────────────── -->
	{#if expanded}
		<div
			transition:slide={{ duration: 180 }}
			class="border-t-2 border-violet-200 dark:border-violet-800/50 bg-violet-50/30 dark:bg-violet-900/10 px-4 py-3 space-y-4"
		>
			{#if detailLoading}
				<div class="text-xs text-gray-500 dark:text-gray-400">Loading details…</div>
			{:else if detailError}
				<div class="text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded">
					{detailError}
					<button class="ml-2 underline" on:click={loadDetail}>Retry</button>
				</div>
			{:else}
				<!-- Tags -->
				{#if detail?.content_tags && detail.content_tags.length > 0}
					<section>
						<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1.5">
							Tags
						</p>
						<div class="flex flex-wrap gap-1">
							{#each detail.content_tags as tag}
								<span class="text-[11px] px-2 py-0.5 rounded-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300">
									{tag}
								</span>
							{/each}
						</div>
					</section>
				{/if}

				<!-- Impact score (companion to confidence which is in the preview) -->
				{#if detail?.impact_score != null}
					<section>
						<div class="flex items-center justify-between mb-0.5">
							<span class="text-[11px] text-gray-500 dark:text-gray-400 font-medium">Impact</span>
							<span class="text-[11px] font-bold text-gray-700 dark:text-gray-200">
								{fmtPct(detail.impact_score)}
							</span>
						</div>
						<div class="h-1.5 bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
							<div
								class="h-full bg-violet-500 dark:bg-violet-600 rounded-full"
								style={`width: ${pctOf(detail.impact_score)}%`}
							></div>
						</div>
					</section>
				{/if}

				<!-- Full key drivers -->
				{#if memo?.key_drivers?.length}
					<section>
						<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1.5">
							All Key Drivers ({memo.key_drivers.length})
						</p>
						<ul class="space-y-1.5">
							{#each memo.key_drivers as d, i (i)}
								{@const arrow = driverArrow(d)}
								<li class="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300 leading-snug">
									<span
										class="flex-shrink-0 mt-0.5 inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-semibold {d.importance === 'high' ? 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300' : d.importance === 'medium' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'}"
									>
										{i + 1}
									</span>
									<span class={`font-bold mt-0.5 ${arrow.cls}`}>{arrow.sym}</span>
									<span>{d.description}</span>
								</li>
							{/each}
						</ul>
					</section>
				{/if}

				<!-- Evidence -->
				{#if combinedEvidence.length > 0}
					<section>
						<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">
							Evidence ({combinedEvidence.length})
						</p>
						<div class="space-y-3">
							{#each combinedEvidence as ev, evIdx (ev.url)}
								<div class="rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 group/chart relative">
									<div class="bg-gray-50 dark:bg-gray-850 px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between gap-2">
										<span class="truncate">{ev.claim}</span>
										<button
											type="button"
											class="flex-shrink-0 text-gray-400 hover:text-violet-600 dark:hover:text-violet-400 transition-colors"
											on:click|stopPropagation={() => (lightboxIndex = evIdx)}
											title="View larger"
											aria-label="Maximize chart"
										>
											<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-5h-4m4 0v4m0-4l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
											</svg>
										</button>
									</div>
									{#if isHtmlAsset(ev.url)}
										<!-- Plotly/VChart iframe with responsive proxy patch.
										     The "open lightbox" overlay sits over the chart so
										     clicks on the chart itself still go to Plotly's
										     own zoom/hover, but the dedicated button on the
										     header bar opens our fullscreen view. -->
										<div
											class="w-full bg-white relative"
											style="aspect-ratio: 5 / 3; min-height: 28rem;"
										>
											<iframe
												src={assetUrl(ev.url)}
												title={ev.claim}
												class="absolute inset-0 w-full h-full border-0 bg-white"
												sandbox="allow-scripts"
												scrolling="no"
											></iframe>
											<!-- Hover-triggered "expand" hint over the chart;
											     pointer-events: none so it never blocks chart
											     interactions like tooltips. -->
											<div
												class="absolute top-2 right-2 px-2 py-1 rounded bg-black/60 text-white text-[10px] opacity-0 group-hover/chart:opacity-100 transition-opacity pointer-events-none"
											>
												Click ⤢ to expand
											</div>
										</div>
									{:else}
										<button
											type="button"
											class="block w-full"
											on:click={() => (lightboxIndex = evIdx)}
											aria-label="Open chart at full size"
										>
											<img
												src={assetUrl(ev.url)}
												alt={ev.claim}
												class="w-full max-h-[36rem] object-contain bg-gray-50 dark:bg-gray-850 cursor-zoom-in"
												on:error={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')}
											/>
										</button>
									{/if}
								</div>
							{/each}
						</div>
					</section>
				{/if}

				<!-- Options & Trade-offs -->
				{#if memo?.options_tradeoffs?.length}
					<section>
						<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">
							Options & Trade-offs
						</p>
						<div class="space-y-2">
							{#each memo.options_tradeoffs as opt, i (i)}
								<div class="border border-gray-200 dark:border-gray-700 rounded-lg p-3 bg-white dark:bg-gray-900">
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

				<!-- Scope -->
				{#if memo?.scope_assumptions && (memo.scope_assumptions.coverage_scope || memo.scope_assumptions.exclusions?.length || memo.scope_assumptions.assumptions?.length)}
					<section>
						<p class="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1.5">
							Scope & Assumptions
						</p>
						<div class="text-xs text-gray-600 dark:text-gray-400 space-y-1.5 leading-relaxed">
							{#if memo.scope_assumptions.coverage_scope}
								<p><span class="font-semibold">Coverage:</span> {memo.scope_assumptions.coverage_scope}</p>
							{/if}
							{#if memo.scope_assumptions.exclusions?.length}
								<p><span class="font-semibold">Exclusions:</span> {memo.scope_assumptions.exclusions.join(', ')}</p>
							{/if}
							{#if memo.scope_assumptions.assumptions?.length}
								<p><span class="font-semibold">Assumptions:</span> {memo.scope_assumptions.assumptions.join(', ')}</p>
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
							{#each memo.caveats as c}<li>{c}</li>{/each}
						</ul>
					</section>
				{/if}

				{#if !memo && combinedEvidence.length === 0 && !detail?.content_tags?.length}
					<p class="text-xs text-gray-400 dark:text-gray-500 italic">
						No additional memo content available for this insight.
					</p>
				{/if}
			{/if}
		</div>
	{/if}
</article>

{#if lightboxIndex !== null && lightboxCharts.length > 0}
	<Lightbox
		charts={lightboxCharts}
		index={lightboxIndex}
		on:close={() => (lightboxIndex = null)}
	/>
{/if}
