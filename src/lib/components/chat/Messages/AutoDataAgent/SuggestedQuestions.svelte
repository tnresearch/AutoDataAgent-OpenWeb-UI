<script lang="ts">
	import { onMount } from 'svelte';
	import {
		suggestQuestionsFromSources,
		type QuestionSuggestion
	} from '$lib/apis/auto-data-agent';
	import { selectedSourceIds } from '$lib/stores/auto-data-agent';

	let token = '';
	$: token = (typeof localStorage !== 'undefined' && localStorage.token) || '';

	let suggestions: QuestionSuggestion[] = [];
	let loading = false;
	let error: string | null = null;
	let lastFetchedKey = '';

	$: ids = $selectedSourceIds;
	$: cacheKey = ids.slice().sort().join(',');
	$: if (token && ids.length > 0 && cacheKey !== lastFetchedKey) {
		lastFetchedKey = cacheKey;
		fetchSuggestions(ids);
	}
	$: if (ids.length === 0 && suggestions.length > 0) {
		suggestions = [];
		lastFetchedKey = '';
	}

	async function fetchSuggestions(sourceIds: string[]) {
		loading = true;
		error = null;
		try {
			const res = await suggestQuestionsFromSources(token, sourceIds, 5);
			suggestions = (res.questions ?? []).filter((s) => s?.question);
		} catch (e: any) {
			error = e?.message ?? String(e);
			suggestions = [];
		} finally {
			loading = false;
		}
	}

	function insertIntoInput(text: string) {
		const textarea = document.querySelector<HTMLTextAreaElement>('textarea');
		if (textarea) {
			const native = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')
				?.set;
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
</script>

{#if ids.length > 0}
	<div class="mt-4 max-w-xl mx-auto px-2">
		{#if loading}
			<p class="text-xs text-gray-400 dark:text-gray-500 text-center">
				Suggesting questions for your data…
			</p>
		{:else if error}
			<p class="text-xs text-red-500 text-center">{error}</p>
		{:else if suggestions.length > 0}
			<p class="text-xs text-gray-500 dark:text-gray-400 mb-2 text-center">
				Try one of these to get started:
			</p>
			<div class="flex flex-wrap gap-2 justify-center">
				{#each suggestions as s (s.question)}
					<button
						type="button"
						class="text-xs px-3 py-1.5 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200 hover:border-violet-300 dark:hover:border-violet-700 hover:bg-violet-50 dark:hover:bg-violet-900/20 transition-colors text-left"
						title={s.intent ?? s.category ?? ''}
						on:click={() => insertIntoInput(s.question)}
					>
						{s.question}
					</button>
				{/each}
			</div>
		{/if}
	</div>
{/if}
