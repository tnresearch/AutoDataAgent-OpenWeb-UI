<script lang="ts">
	import { assetUrl, type ChartAsset } from '$lib/apis/auto-data-agent';
	import { createEventDispatcher } from 'svelte';

	export let chart: ChartAsset;

	const dispatch = createEventDispatcher<{ open: ChartAsset }>();

	let imgLoaded = false;
	let imgFailed = false;

	$: src = chart.png_url ? assetUrl(chart.png_url) : '';
	// Some chart engines (notably Plotly) only emit interactive HTML rather
	// than a static PNG. The asset path still ends in .html, so detect it
	// up-front and render a different thumbnail strategy.
	$: isInteractive = !!chart.png_url && /\.html?(\?|#|$)/i.test(chart.png_url);
</script>

<button
	type="button"
	class="ada-chart-card group text-left border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden bg-white dark:bg-gray-900 hover:shadow-md hover:border-violet-300 dark:hover:border-violet-700 transition-all cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
	on:click={() => dispatch('open', chart)}
>
	<div class="relative h-36 bg-gray-50 dark:bg-gray-850 overflow-hidden">
		{#if isInteractive}
			<!-- Plotly / D3 HTML chart: show a styled placeholder. Loading the
			     full iframe just for a thumbnail is wasteful, and the lightbox
			     shows the live interactive chart on click. -->
			<div
				class="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-violet-50 to-blue-50 dark:from-violet-900/20 dark:to-blue-900/20"
			>
				<div class="text-center px-3">
					<svg
						class="w-8 h-8 mx-auto text-violet-400 dark:text-violet-300 mb-1"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="1.5"
							d="M3 3v18h18 M7 14l3-3 4 4 5-7"
						/>
					</svg>
					<div class="text-[11px] text-violet-700 dark:text-violet-300 font-medium">
						Interactive chart
					</div>
					<div class="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">click to open</div>
				</div>
			</div>
		{:else if imgFailed}
			<div class="absolute inset-0 flex items-center justify-center text-xs text-gray-400 dark:text-gray-600">
				Chart unavailable
			</div>
		{:else if src}
			{#if !imgLoaded}
				<div class="absolute inset-0 animate-pulse bg-gradient-to-br from-gray-100 to-gray-200 dark:from-gray-800 dark:to-gray-850"></div>
			{/if}
			<img
				{src}
				alt={chart.title}
				class="w-full h-full object-cover transition-transform duration-200 group-hover:scale-[1.02]"
				class:opacity-0={!imgLoaded}
				class:opacity-100={imgLoaded}
				on:load={() => (imgLoaded = true)}
				on:error={() => (imgFailed = true)}
			/>
		{/if}
	</div>
	<div class="px-3 py-2">
		<p class="text-xs font-medium text-gray-700 dark:text-gray-200 truncate">{chart.title}</p>
		{#if chart.interpretation}
			<p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5 line-clamp-2">
				{chart.interpretation}
			</p>
		{/if}
	</div>
</button>

<style>
	.ada-chart-card :global(img) {
		display: block;
	}
</style>
