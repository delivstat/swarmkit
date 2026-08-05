/**
 * Turning the durable job history into what a dashboard should answer.
 *
 * The dashboard read `/jobs` — the in-memory store, holding only what the current serve process
 * started via `POST /run/{topology}` and emptied on restart. So a workspace whose work came from
 * the CLI, a pipeline or chat showed an empty dashboard, and a restart erased whatever was there.
 * That is the "stale analytics": not stale numbers, but the wrong source.
 *
 * Everything here reads `/jobs/history`, which is durable and carries every path's runs plus their
 * tokens and cost.
 */

import type { PersistedJob } from "./types";

/** Where a run came from. `null` for rows written before `source` existed — shown as such rather
 * than guessed at, since a wrong attribution is worse than an honest unknown. */
export type RunSource = "serve" | "cli" | "pipeline" | "chat" | null;

export interface ActivitySummary {
	total: number;
	running: number;
	completed: number;
	failed: number;
	/** Share of finished runs that failed, 0–1. Null when nothing has finished — a workspace with
	 * no completed runs has no failure rate, and showing 0% would claim otherwise. */
	failureRate: number | null;
	costUsd: number;
	inputTokens: number;
	outputTokens: number;
}

const IN_FLIGHT = new Set(["pending", "running"]);

/** Runs from the last `hours`, newest first. History is already ordered, so this only cuts. */
export function withinHours(
	jobs: PersistedJob[],
	hours: number,
	now: number,
): PersistedJob[] {
	const cutoff = now - hours * 3600_000;
	return jobs.filter((j) => {
		const t = Date.parse(j.created_at);
		return Number.isNaN(t) ? false : t >= cutoff;
	});
}

export function summarize(jobs: PersistedJob[]): ActivitySummary {
	let running = 0;
	let completed = 0;
	let failed = 0;
	let costUsd = 0;
	let inputTokens = 0;
	let outputTokens = 0;

	for (const job of jobs) {
		if (IN_FLIGHT.has(job.status)) running++;
		else if (job.status === "completed") completed++;
		else if (job.status === "failed") failed++;
		costUsd += job.usage_cost_usd ?? 0;
		inputTokens += job.usage_input_tokens ?? 0;
		outputTokens += job.usage_output_tokens ?? 0;
	}

	const finished = completed + failed;
	return {
		total: jobs.length,
		running,
		completed,
		failed,
		failureRate: finished > 0 ? failed / finished : null,
		costUsd,
		inputTokens,
		outputTokens,
	};
}

export interface SourceBreakdown {
	source: RunSource;
	runs: number;
	costUsd: number;
}

/** Runs and spend per front door, biggest spender first — the question "where is this coming
 * from?", which nothing could answer while every path looked alike. */
export function bySource(jobs: PersistedJob[]): SourceBreakdown[] {
	const acc = new Map<string, SourceBreakdown>();
	for (const job of jobs) {
		const key = job.source ?? "";
		const row = acc.get(key) ?? {
			source: (job.source ?? null) as RunSource,
			runs: 0,
			costUsd: 0,
		};
		row.runs++;
		row.costUsd += job.usage_cost_usd ?? 0;
		acc.set(key, row);
	}
	return [...acc.values()].sort(
		(a, b) => b.costUsd - a.costUsd || b.runs - a.runs,
	);
}

export interface TopologyCost {
	topology: string;
	runs: number;
	costUsd: number;
	failed: number;
}

/** Spend per topology, biggest first. The actionable version of a cost total: which swarm. */
export function byTopology(jobs: PersistedJob[], limit = 5): TopologyCost[] {
	const acc = new Map<string, TopologyCost>();
	for (const job of jobs) {
		const row = acc.get(job.topology) ?? {
			topology: job.topology,
			runs: 0,
			costUsd: 0,
			failed: 0,
		};
		row.runs++;
		row.costUsd += job.usage_cost_usd ?? 0;
		if (job.status === "failed") row.failed++;
		acc.set(job.topology, row);
	}
	return [...acc.values()]
		.sort((a, b) => b.costUsd - a.costUsd || b.runs - a.runs)
		.slice(0, limit);
}

/** The most recent failures — what a dashboard should surface unprompted. */
export function recentFailures(
	jobs: PersistedJob[],
	limit = 5,
): PersistedJob[] {
	return jobs.filter((j) => j.status === "failed").slice(0, limit);
}

/** A percentage for display, or a dash when there is no rate to state. */
export function formatRate(rate: number | null): string {
	return rate === null ? "-" : `${Math.round(rate * 100)}%`;
}

/** Compact token counts — a dashboard cell has no room for eight digits. */
export function formatCount(n: number): string {
	if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
	if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
	return `${n}`;
}

/** Cost for display. Distinguishes "nothing recorded" from "recorded as free": a workspace whose
 * providers report no cost should not read the same as one that has not run. */
export function formatSpend(usd: number, runs: number): string {
	if (runs === 0) return "-";
	if (usd === 0) return "$0.00";
	if (usd < 0.01) return "<$0.01";
	return `$${usd.toFixed(2)}`;
}

/** Label for a source, including the honest unknown. */
export function sourceLabel(source: RunSource): string {
	switch (source) {
		case "serve":
			return "API";
		case "cli":
			return "CLI";
		case "pipeline":
			return "Pipeline";
		case "chat":
			return "Chat";
		default:
			return "unattributed";
	}
}
