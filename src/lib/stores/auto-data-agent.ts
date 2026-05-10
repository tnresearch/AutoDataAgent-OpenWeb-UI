/**
 * Cross-component state for the AutoDataAgent integration.
 *
 * Tracks the user's data-source selection (IDs) plus a label cache that
 * survives reloads — so we can show "Sources: Actuals 2025, Attrition…"
 * even before the connections list has been fetched.
 */

import { writable } from 'svelte/store';

const KEY_IDS = 'ada-selected-source-ids';
const KEY_LABELS = 'ada-source-labels';

type LabelMap = Record<string, string>;

function loadIds(): string[] {
	if (typeof localStorage === 'undefined') return [];
	try {
		const raw = localStorage.getItem(KEY_IDS);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? parsed.filter((s) => typeof s === 'string') : [];
	} catch {
		return [];
	}
}

function loadLabels(): LabelMap {
	if (typeof localStorage === 'undefined') return {};
	try {
		const raw = localStorage.getItem(KEY_LABELS);
		if (!raw) return {};
		const parsed = JSON.parse(raw);
		return parsed && typeof parsed === 'object' ? parsed : {};
	} catch {
		return {};
	}
}

export const selectedSourceIds = writable<string[]>(loadIds());

selectedSourceIds.subscribe((value) => {
	if (typeof localStorage === 'undefined') return;
	try {
		localStorage.setItem(KEY_IDS, JSON.stringify(value));
	} catch {
		/* private mode etc */
	}
});

/**
 * Source-id → human-readable name. Updated whenever a connection's source
 * list is fetched. Persisted so the chat header chip stays informative
 * across reloads even before connections are loaded.
 */
export const sourceLabels = writable<LabelMap>(loadLabels());

sourceLabels.subscribe((value) => {
	if (typeof localStorage === 'undefined') return;
	try {
		localStorage.setItem(KEY_LABELS, JSON.stringify(value));
	} catch {
		/* private mode etc */
	}
});

/** Merge new labels into the cache without dropping existing ones. */
export function rememberSourceLabels(labels: LabelMap) {
	sourceLabels.update((current) => ({ ...current, ...labels }));
}
