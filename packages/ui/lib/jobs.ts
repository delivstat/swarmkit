/**
 * Splitting the two job stores into what a reader should see.
 *
 * `/jobs` is the in-memory JobStore — this serve process only, gone on restart. `/jobs/history` is
 * the durable store, and also carries token usage and cost. The jobs page used only the first,
 * which is why it showed nothing but in-flight work and lost everything when serve restarted.
 *
 * A job is written to BOTH stores at creation (`_services.py` persists it as it starts), so the
 * lists overlap for as long as it runs. Rendering them as two tables without accounting for that
 * would print every running job twice.
 */

import type { JobListItem, PersistedJob } from "./types";

export interface JobSections {
	/** In flight on this server, newest first. */
	running: JobListItem[];
	/** Durable rows, excluding anything already shown as running. Newest first. */
	past: PersistedJob[];
}

const IN_FLIGHT = new Set(["pending", "running"]);

/** Partition the two sources into the two sections, de-duplicating the overlap. */
export function jobSections(
	live: JobListItem[] | null,
	history: PersistedJob[] | null,
): JobSections {
	const running = (live ?? []).filter((j) => IN_FLIGHT.has(j.status));
	const shownLive = new Set(running.map((j) => j.job_id));
	return {
		// `/jobs` returns oldest-first; the newest job is the one a reader is looking for.
		running: [...running].reverse(),
		// `/jobs/history` is already newest-first (ORDER BY created_at DESC).
		past: (history ?? []).filter((j) => !shownLive.has(j.job_id)),
	};
}

/** Cost for display. A dash for "not recorded" — an unmeasured run and a free one differ. */
export function formatCost(usd: number | null | undefined): string {
	if (usd === null || usd === undefined) return "-";
	if (usd > 0 && usd < 0.01) return "<$0.01";
	return `$${usd.toFixed(2)}`;
}

/** "in / out" token counts, or a dash when neither was recorded. */
export function formatTokens(
	input: number | null | undefined,
	output: number | null | undefined,
): string {
	if ((input ?? null) === null && (output ?? null) === null) return "-";
	return `${(input ?? 0).toLocaleString()} / ${(output ?? 0).toLocaleString()}`;
}

/** A timestamp for display, or a dash when absent (a job that has not finished). */
export function formatWhen(value: string | null | undefined): string {
	return value ? new Date(value).toLocaleString() : "-";
}
