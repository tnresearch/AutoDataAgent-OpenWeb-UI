<script lang="ts">
	import { onMount, tick } from 'svelte';
	import {
		listConnections,
		listSources,
		type DataConnection,
		type DataSource
	} from '$lib/apis/auto-data-agent';
	import {
		selectedSourceIds,
		sourceLabels,
		rememberSourceLabels
	} from '$lib/stores/auto-data-agent';

	export let triggerLabel: string | null = null; // override the button text
	export let compact: boolean = false; // smaller chip variant for chat header

	let token = '';
	$: token = (typeof localStorage !== 'undefined' && localStorage.token) || '';

	let connections: DataConnection[] = [];
	let connectionLoading = false;
	let connectionError: string | null = null;
	let connectionFilter = '';

	let sourcesByConnection: Record<string, DataSource[]> = {};
	let loadingConnIds = new Set<string>();
	let expandedConnIds = new Set<string>();
	let expandedSourceIds = new Set<string>();

	let modalOpen = false;
	let selected: Set<string> = new Set();

	// Keep the local selection in sync with the store while the modal is closed.
	$: if (!modalOpen) selected = new Set($selectedSourceIds);

	$: filteredConnections = (() => {
		const q = connectionFilter.trim().toLowerCase();
		const list = q
			? connections.filter(
					(c) =>
						c.name.toLowerCase().includes(q) ||
						(c.description ?? '').toLowerCase().includes(q)
				)
			: connections.slice();
		// Starred first, then by name
		list.sort((a, b) => {
			if (a.is_starred !== b.is_starred) return a.is_starred ? -1 : 1;
			return a.name.localeCompare(b.name);
		});
		return list;
	})();

	$: chipLabel = (() => {
		if (triggerLabel != null) return triggerLabel;
		const ids = $selectedSourceIds;
		if (ids.length === 0) return 'Pick database sources';
		const labels = $sourceLabels;
		const named = ids.map((id) => labels[id]).filter((s): s is string => !!s);
		if (named.length >= 2) return `Sources: ${named.length} picked`;
		if (named.length === 1) return `Sources: ${named[0]}`;
		// Fallback: just count when labels haven't loaded yet
		return `Sources: ${ids.length} picked`;
	})();

	async function loadConnections() {
		if (!token) return;
		connectionLoading = true;
		connectionError = null;
		try {
			const res = await listConnections(token);
			connections = res.connections ?? [];
		} catch (e: any) {
			connectionError = e?.message ?? String(e);
		} finally {
			connectionLoading = false;
		}
	}

	async function ensureSourcesLoaded(connectionId: string): Promise<DataSource[]> {
		if (sourcesByConnection[connectionId]) return sourcesByConnection[connectionId];
		loadingConnIds.add(connectionId);
		loadingConnIds = new Set(loadingConnIds);
		try {
			const sources = await listSources(token, connectionId);
			sourcesByConnection[connectionId] = sources;
			sourcesByConnection = { ...sourcesByConnection };
			// Cache labels so the trigger button stays informative across reloads.
			const labels: Record<string, string> = {};
			for (const s of sources) labels[s.source_id] = s.display_name || s.source_name;
			rememberSourceLabels(labels);
			return sources;
		} catch (e) {
			console.warn('Auto Data Analyst — list sources failed', e);
			sourcesByConnection[connectionId] = [];
			return [];
		} finally {
			loadingConnIds.delete(connectionId);
			loadingConnIds = new Set(loadingConnIds);
		}
	}

	async function toggleConnection(conn: DataConnection) {
		if (expandedConnIds.has(conn.connection_id)) {
			expandedConnIds.delete(conn.connection_id);
		} else {
			expandedConnIds.add(conn.connection_id);
			await ensureSourcesLoaded(conn.connection_id);
		}
		expandedConnIds = new Set(expandedConnIds);
	}

	function toggleSource(sourceId: string) {
		const next = new Set(selected);
		if (next.has(sourceId)) next.delete(sourceId);
		else next.add(sourceId);
		selected = next;
	}

	// Synchronous: sources are always pre-loaded by openModal before the user
	// can interact. Keeping this synchronous avoids Svelte re-rendering during
	// an await and transiently resetting the checkbox to its pre-click state.
	function toggleAllInConnection(conn: DataConnection) {
		const sources = sourcesByConnection[conn.connection_id] ?? [];
		const next = new Set(selected);
		const allSelected = sources.length > 0 && sources.every((s) => next.has(s.source_id));
		if (allSelected) {
			for (const s of sources) next.delete(s.source_id);
		} else {
			for (const s of sources) next.add(s.source_id);
		}
		selected = next;
	}

	// Svelte action that sets both .checked and .indeterminate as DOM properties.
	// Neither can be reliably controlled via HTML attributes:
	//  - setAttribute('checked', …) only affects the default state, not current state
	//  - 'indeterminate' has no HTML attribute counterpart at all
	function triCheckbox(node: HTMLInputElement, state: 'none' | 'partial' | 'all') {
		function apply(s: 'none' | 'partial' | 'all') {
			node.checked = s === 'all';
			node.indeterminate = s === 'partial';
		}
		apply(state);
		return { update: apply };
	}

	function connectionSelectionState(conn: DataConnection): 'none' | 'partial' | 'all' {
		const sources = sourcesByConnection[conn.connection_id];
		if (!sources || sources.length === 0) return 'none';
		const picked = sources.filter((s) => selected.has(s.source_id)).length;
		if (picked === 0) return 'none';
		if (picked === sources.length) return 'all';
		return 'partial';
	}

	function applySelection() {
		// Persist labels for everything currently selected so the chip stays
		// readable even when only a subset of connections have been loaded.
		const labels: Record<string, string> = {};
		for (const arr of Object.values(sourcesByConnection)) {
			for (const s of arr) {
				if (selected.has(s.source_id)) labels[s.source_id] = s.display_name || s.source_name;
			}
		}
		rememberSourceLabels(labels);
		selectedSourceIds.set(Array.from(selected));
		modalOpen = false;
	}

	function clearSelection() {
		selectedSourceIds.set([]);
		selected = new Set();
	}

	async function openModal() {
		modalOpen = true;
		if (connections.length === 0) await loadConnections();
		// Always pre-load sources for every connection so parent checkboxes reflect
		// accurate tri-state. Without this, connectionSelectionState() returns 'none'
		// for connections whose sources haven't been fetched yet, causing the parent
		// checkbox to appear unchecked even when all children are selected — and a
		// subsequent click then deselects everything instead of confirming the selection.
		// ensureSourcesLoaded is idempotent: already-loaded connections return immediately.
		await Promise.all(connections.map((c) => ensureSourcesLoaded(c.connection_id)));
		await tick();
	}

	function toggleSourceExpanded(sourceId: string) {
		const next = new Set(expandedSourceIds);
		if (next.has(sourceId)) next.delete(sourceId);
		else next.add(sourceId);
		expandedSourceIds = next;
	}

	function getColumns(src: DataSource): { name: string; dtype?: string }[] {
		const meta = src.schema_metadata as any;
		if (!meta) return [];
		const cols = meta.columns ?? meta.fields ?? meta.schema ?? [];
		if (!Array.isArray(cols)) return [];
		return cols
			.map((c: any) => {
				if (typeof c === 'string') return { name: c };
				return { name: c.name ?? c.column ?? '?', dtype: c.dtype ?? c.type ?? c.data_type };
			})
			.filter((c) => !!c.name);
	}
</script>

<!-- Trigger -->
<div class="flex items-center justify-center gap-2 flex-wrap" class:mt-2={!compact}>
	<button
		type="button"
		class="rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200 hover:border-violet-300 dark:hover:border-violet-700 hover:bg-violet-50 dark:hover:bg-violet-900/20 transition-colors flex items-center gap-1.5"
		class:text-xs={!compact}
		class:px-3={!compact}
		class:py-1.5={!compact}
		class:text-[11px]={compact}
		class:px-2={compact}
		class:py-1={compact}
		on:click={openModal}
	>
		<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path
				stroke-linecap="round"
				stroke-linejoin="round"
				stroke-width="2"
				d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3 -3.6 3-8 3-8-1.3-8-3z M4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"
			/>
		</svg>
		{chipLabel}
	</button>
	{#if $selectedSourceIds.length > 0}
		<button
			type="button"
			class="text-xs text-gray-400 hover:text-red-500 transition-colors"
			on:click={clearSelection}
			title="Clear data-source selection"
		>
			✕ clear
		</button>
	{/if}
</div>

{#if modalOpen}
	<div
		class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-6"
		on:click={() => (modalOpen = false)}
		role="dialog"
		aria-modal="true"
	>
		<div
			class="bg-white dark:bg-gray-900 rounded-xl shadow-2xl w-full max-w-xl max-h-[80vh] flex flex-col overflow-hidden border border-gray-200 dark:border-gray-700"
			on:click|stopPropagation
		>
			<div class="px-5 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between gap-3">
				<h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100 flex-shrink-0">
					Select database sources
				</h3>
				<input
					type="text"
					bind:value={connectionFilter}
					placeholder="Filter connections…"
					class="flex-1 max-w-[14rem] text-xs px-2 py-1 rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200 focus:outline-none focus:border-violet-400 dark:focus:border-violet-600"
				/>
				<button
					type="button"
					class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
					on:click={() => (modalOpen = false)}
					aria-label="Close"
				>
					✕
				</button>
			</div>

			<div class="flex-1 overflow-y-auto px-3 py-3 space-y-2">
				{#if connectionLoading}
					<div class="text-xs text-gray-500 dark:text-gray-400 px-3 py-6 text-center">
						Loading connections…
					</div>
				{:else if connectionError}
					<div class="text-xs text-red-600 dark:text-red-400 px-3 py-3 bg-red-50 dark:bg-red-900/20 rounded">
						{connectionError}
					</div>
				{:else if filteredConnections.length === 0}
					<div class="text-xs text-gray-500 dark:text-gray-400 px-3 py-6 text-center">
						{connectionFilter ? 'No connections match the filter.' : 'No data connections registered.'}
					</div>
				{:else}
					{#each filteredConnections as conn (conn.connection_id)}
						{@const state = connectionSelectionState(conn)}
						<div class="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
							<div
								class="flex items-center gap-2 px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
							>
								<!-- Tri-state checkbox for connection-wide select.
								     use:triCheckbox sets .checked and .indeterminate as DOM
								     properties on every update — the only correct way in Svelte. -->
								<input
									type="checkbox"
									class="accent-violet-600"
									use:triCheckbox={state}
									on:change={() => toggleAllInConnection(conn)}
									on:click|stopPropagation
									aria-label={`Select all sources in ${conn.name}`}
								/>
								<button
									type="button"
									class="flex-1 text-left flex items-center justify-between min-w-0"
									on:click={() => toggleConnection(conn)}
								>
									<div class="min-w-0 flex-1">
										<div class="text-sm font-medium text-gray-800 dark:text-gray-100 truncate flex items-center gap-1.5">
											{#if conn.is_starred}
												<span class="text-amber-400" title="Starred">★</span>
											{/if}
											{conn.name}
										</div>
										<div class="text-xs text-gray-500 dark:text-gray-400">
											{conn.connection_type} · {conn.source_count} sources
										</div>
									</div>
									<svg
										class="w-4 h-4 text-gray-400 transition-transform flex-shrink-0"
										class:rotate-90={expandedConnIds.has(conn.connection_id)}
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
									</svg>
								</button>
							</div>
							{#if expandedConnIds.has(conn.connection_id)}
								<div class="px-3 pb-2 pt-1 bg-gray-50 dark:bg-gray-850 border-t border-gray-200 dark:border-gray-700">
									{#if loadingConnIds.has(conn.connection_id)}
										<div class="text-xs text-gray-500 dark:text-gray-400 py-2">Loading sources…</div>
									{:else if (sourcesByConnection[conn.connection_id]?.length ?? 0) === 0}
										<div class="text-xs text-gray-500 dark:text-gray-400 py-2">No sources.</div>
									{:else}
										<div class="space-y-1">
											{#each sourcesByConnection[conn.connection_id] ?? [] as src (src.source_id)}
												{@const cols = getColumns(src)}
												<div
													class="rounded hover:bg-white dark:hover:bg-gray-800 transition-colors"
												>
													<label class="flex items-start gap-2 text-sm cursor-pointer py-1 px-1">
														<input
															type="checkbox"
															class="mt-0.5 accent-violet-600 flex-shrink-0"
															checked={selected.has(src.source_id)}
															on:change={() => toggleSource(src.source_id)}
														/>
														<div class="min-w-0 flex-1">
															<div class="text-gray-800 dark:text-gray-100 truncate">
																{src.display_name || src.source_name}
															</div>
															{#if src.description}
																<div class="text-xs text-gray-400 dark:text-gray-500 truncate">
																	{src.description}
																</div>
															{/if}
														</div>
														{#if cols.length > 0}
															<button
																type="button"
																class="text-xs text-gray-400 hover:text-violet-600 dark:hover:text-violet-400 px-1 flex-shrink-0"
																on:click|preventDefault|stopPropagation={() =>
																	toggleSourceExpanded(src.source_id)}
																title={`Show ${cols.length} columns`}
															>
																{cols.length} cols ▾
															</button>
														{/if}
														{#if src.row_count}
															<div class="text-xs text-gray-400 dark:text-gray-500 whitespace-nowrap flex-shrink-0">
																{src.row_count.toLocaleString()} rows
															</div>
														{/if}
													</label>
													{#if expandedSourceIds.has(src.source_id) && cols.length > 0}
														<div class="ml-6 mr-2 mb-1 px-2 py-1 rounded bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
															<div class="flex flex-wrap gap-1">
																{#each cols.slice(0, 30) as col (col.name)}
																	<span
																		class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 font-mono"
																		title={col.dtype}
																	>
																		{col.name}
																		{#if col.dtype}<span class="text-gray-400">·{col.dtype}</span
																			>{/if}
																	</span>
																{/each}
																{#if cols.length > 30}
																	<span class="text-[10px] text-gray-400">+{cols.length - 30} more</span>
																{/if}
															</div>
														</div>
													{/if}
												</div>
											{/each}
										</div>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				{/if}
			</div>

			<div class="px-5 py-3 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between">
				<div class="text-xs text-gray-500 dark:text-gray-400">
					{selected.size} selected
				</div>
				<div class="flex items-center gap-2">
					<button
						type="button"
						class="text-xs px-3 py-1.5 rounded text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
						on:click={() => (modalOpen = false)}>Cancel</button
					>
					<button
						type="button"
						class="text-xs px-3 py-1.5 rounded bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
						on:click={applySelection}>Apply</button
					>
				</div>
			</div>
		</div>
	</div>
{/if}
