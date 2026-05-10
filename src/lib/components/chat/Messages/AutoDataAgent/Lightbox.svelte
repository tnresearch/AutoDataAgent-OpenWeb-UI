<script lang="ts">
	import { assetUrl, type ChartAsset } from '$lib/apis/auto-data-agent';
	import { createEventDispatcher, onDestroy, onMount } from 'svelte';

	export let charts: ChartAsset[] = [];
	export let index: number = 0;

	const dispatch = createEventDispatcher<{ close: void }>();

	$: chart = charts[index];
	$: isInteractive = !!chart?.png_url && /\.html?(\?|#|$)/i.test(chart.png_url);

	function next() {
		index = (index + 1) % charts.length;
	}
	function prev() {
		index = (index - 1 + charts.length) % charts.length;
	}

	function handleKey(e: KeyboardEvent) {
		if (e.key === 'Escape') dispatch('close');
		else if (e.key === 'ArrowRight') next();
		else if (e.key === 'ArrowLeft') prev();
	}

	onMount(() => {
		window.addEventListener('keydown', handleKey);
	});
	onDestroy(() => {
		window.removeEventListener('keydown', handleKey);
	});

	// Svelte action: teleport the element to <body> so position:fixed escapes
	// any transformed ancestor (Open WebUI's chat container uses transforms
	// for animations, which traps fixed-positioned children inside its box).
	function portal(node: HTMLElement) {
		const target = document.body;
		target.appendChild(node);
		// Lock background scroll while open.
		const prevOverflow = document.documentElement.style.overflow;
		document.documentElement.style.overflow = 'hidden';
		return {
			destroy() {
				try { target.removeChild(node); } catch (_) {}
				document.documentElement.style.overflow = prevOverflow;
			}
		};
	}
</script>

<div
	use:portal
	class="fixed inset-0 bg-black/80 z-[9999] flex items-center justify-center p-8"
	on:click={() => dispatch('close')}
	role="dialog"
	aria-modal="true"
>
	{#if charts.length > 1}
		<button
			class="absolute left-4 top-1/2 -translate-y-1/2 text-white text-3xl leading-none hover:text-gray-300 px-2 py-1"
			on:click|stopPropagation={prev}
			aria-label="Previous chart"
		>
			‹
		</button>
	{/if}

	<div
		class="relative flex items-center justify-center {isInteractive
			? 'w-[90vw] h-[85vh] bg-white dark:bg-gray-900 rounded shadow-xl overflow-hidden'
			: ''}"
		on:click|stopPropagation
	>
		{#if isInteractive}
			<iframe
				src={assetUrl(chart.png_url)}
				title={chart.title}
				class="w-full h-full border-0"
				sandbox="allow-scripts"
			></iframe>
		{:else}
			<img
				src={assetUrl(chart.png_url)}
				alt={chart.title}
				class="max-w-full max-h-[85vh] object-contain rounded shadow-xl"
			/>
		{/if}
		<div
			class="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-sm px-4 py-2 text-center"
			class:rounded-b={!isInteractive}
		>
			{chart.title}
			{#if chart.interpretation}
				<p class="text-xs text-white/70 mt-0.5 line-clamp-1">{chart.interpretation}</p>
			{/if}
		</div>
	</div>

	{#if charts.length > 1}
		<button
			class="absolute right-14 top-1/2 -translate-y-1/2 text-white text-3xl leading-none hover:text-gray-300 px-2 py-1"
			on:click|stopPropagation={next}
			aria-label="Next chart"
		>
			›
		</button>
	{/if}

	<button
		class="absolute top-4 right-4 text-white text-2xl leading-none hover:text-gray-300"
		on:click|stopPropagation={() => dispatch('close')}
		aria-label="Close"
	>
		×
	</button>

	{#if charts.length > 1}
		<div class="absolute bottom-4 left-1/2 -translate-x-1/2 text-white/70 text-sm">
			{index + 1} / {charts.length}
		</div>
	{/if}
</div>
