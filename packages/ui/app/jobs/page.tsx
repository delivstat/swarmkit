"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";

import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatCost, formatTokens, formatWhen, jobSections } from "@/lib/jobs";
import type { JobListItem, PersistedJob, QueueStats } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

/** A compact queue-health strip (queue-observability.md): backlog depth, oldest-unclaimed age and
 * queue-wait p95, read from GET /queue/stats. Hidden until there is something queued or running —
 * an all-in-one server with an empty queue has nothing to say here. */
function QueueStrip() {
	const fetchStats = useCallback(() => api.queueStats(), []);
	const { data } = usePoll<QueueStats>(fetchStats, 4000);
	if (!data || data.queued + data.running === 0) return null;
	const cell = (label: string, value: string) => (
		<div className="flex flex-col">
			<span className="text-lg font-semibold tabular-nums">{value}</span>
			<span className="text-xs text-muted-foreground">{label}</span>
		</div>
	);
	const topos = Object.entries(data.depth_by_topology).sort(
		(a, b) => b[1] - a[1],
	);
	return (
		<section className="rounded-lg border bg-muted/30 p-4">
			<div className="mb-3 flex items-baseline gap-3">
				<h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
					Queue
				</h2>
			</div>
			<div className="flex flex-wrap gap-8">
				{cell("queued", String(data.queued))}
				{cell("running", String(data.running))}
				{cell("oldest queued", `${data.oldest_queued_age_seconds.toFixed(0)}s`)}
				{cell("queue wait p95", `${data.queue_wait_p95_seconds.toFixed(1)}s`)}
				{cell("exec p95", `${data.execution_p95_seconds.toFixed(1)}s`)}
			</div>
			{topos.length > 0 && (
				<div className="mt-3 text-xs text-muted-foreground">
					by topology:{" "}
					{topos.map(([t, n], i) => (
						<span key={t}>
							{i > 0 ? " · " : ""}
							<span className="font-mono">{t}</span> {n}
						</span>
					))}
				</div>
			)}
		</section>
	);
}

/**
 * Two sections, because there are two stores.
 *
 * `/jobs` is the in-memory JobStore: this process only, gone on restart. It was the page's only
 * source, which is why the page showed nothing but in-flight work and lost everything when serve
 * restarted. `/jobs/history` is the durable store, and it also carries token usage and cost.
 *
 * A job is written to BOTH at creation, so the lists overlap while it runs — the history table
 * excludes anything currently shown live rather than printing it twice.
 */

function Cell({
	children,
	muted,
}: {
	children: React.ReactNode;
	muted?: boolean;
}) {
	return (
		<td className={`px-4 py-2 ${muted ? "text-xs text-muted-foreground" : ""}`}>
			{children}
		</td>
	);
}

/** A stage's link back to the pipeline run that asked for it. A standalone run has none. */
/**
 * The correlation id a run was started with — a ticket, a requirement, an A2A context. It groups
 * runs; the link narrows this page to that group. (It used to link to `/runs`, the bundled
 * pipeline page, which left with the pipeline layer in 1.189.0.)
 */
function CorrelationLink({ id }: { id: string | null }) {
	if (!id) return <span className="text-muted-foreground">-</span>;
	return (
		<Link
			href={`/jobs?correlation_id=${encodeURIComponent(id)}`}
			className="font-mono text-xs text-sky-500 hover:underline"
		>
			{id}
		</Link>
	);
}

function JobIdLink({ id }: { id: string }) {
	return (
		<Link
			href={`/job?id=${id}`}
			className="font-mono text-xs text-sky-500 hover:underline"
		>
			{id}
		</Link>
	);
}

/**
 * Ask a run to stop (design/details/stopping-a-run.md).
 *
 * The wording matters more than the button. A stop is cooperative — it lands at the next agent
 * boundary and a call in flight finishes first — so the UI says "stopping…" rather than reporting
 * the job dead. An operator who believes a run has stopped and starts a replacement gets two runs
 * writing the same artifacts, which is worse than waiting.
 */
function StopButton({ id, onDone }: { id: string; onDone?: () => void }) {
	const [state, setState] = useState<"idle" | "asking" | "asked" | "error">(
		"idle",
	);
	const [detail, setDetail] = useState("");

	if (state === "asked") {
		return (
			<span
				className="text-xs text-muted-foreground"
				title="It stops between agents; a call in flight finishes first. The run keeps what it has already done and can be resumed."
			>
				stopping…
			</span>
		);
	}

	return (
		<span className="flex items-center justify-end gap-2">
			{state === "error" && (
				<span className="text-xs text-destructive">{detail}</span>
			)}
			<Button
				type="button"
				size="sm"
				variant="outline"
				disabled={state === "asking"}
				onClick={async () => {
					setState("asking");
					try {
						await api.stopJob(id);
						setState("asked");
						onDone?.();
					} catch (err) {
						setDetail(err instanceof Error ? err.message : "stop failed");
						setState("error");
					}
				}}
			>
				{state === "asking" ? "…" : "Stop"}
			</Button>
		</span>
	);
}

function JobsView() {
	const correlationId = useSearchParams().get("correlation_id") ?? "";
	const fetchLive = useCallback(() => api.jobs(), []);
	const fetchHistory = useCallback(
		() => api.jobsHistory(correlationId || undefined),
		[correlationId],
	);

	const live = usePoll<JobListItem[]>(fetchLive, 3000);
	// Slower: durable rows only change when a job starts or finishes, and this table can be long.
	const history = usePoll<PersistedJob[]>(fetchHistory, 15000);

	const { running, past } = useMemo(
		() => jobSections(live.data ?? null, history.data ?? null),
		[live.data, history.data],
	);

	return (
		<div className="space-y-8">
			<QueueStrip />
			<section>
				<div className="mb-4 flex items-baseline gap-3">
					<h2 className="text-xl font-bold">Running now</h2>
					<span className="text-xs text-muted-foreground">
						in flight on this server
					</span>
				</div>
				{live.loading && !live.data && (
					<p className="text-sm text-muted-foreground">Loading…</p>
				)}
				{live.error && <p className="text-sm text-destructive">{live.error}</p>}
				{live.data && running.length === 0 && (
					<p className="text-sm text-muted-foreground">
						Nothing running. Submit a run via POST /run/&#123;topology&#125;.
					</p>
				)}
				{running.length > 0 && (
					<div className="overflow-hidden rounded-lg border">
						<table className="w-full text-sm">
							<thead>
								<tr className="bg-muted text-muted-foreground">
									<th className="px-4 py-2 text-left font-medium">Job ID</th>
									<th className="px-4 py-2 text-left font-medium">Topology</th>
									<th className="px-4 py-2 text-left font-medium">Version</th>
									<th className="px-4 py-2 text-left font-medium">Status</th>
									<th className="px-4 py-2 text-left font-medium">Started</th>
									<th className="px-4 py-2 text-right font-medium">Stop</th>
								</tr>
							</thead>
							<tbody>
								{running.map((job) => (
									<tr
										key={job.job_id}
										className="border-t transition-colors hover:bg-muted/50"
									>
										<Cell>
											<JobIdLink id={job.job_id} />
										</Cell>
										<Cell>{job.topology}</Cell>
										<Cell muted>{job.version ? `v${job.version}` : "-"}</Cell>
										<Cell>
											<StatusBadge status={job.status} />
										</Cell>
										<Cell muted>{formatWhen(job.created_at)}</Cell>
										<Cell>
											<StopButton id={job.job_id} onDone={live.refetch} />
										</Cell>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				)}
			</section>

			<section>
				<div className="mb-4 flex items-baseline gap-3">
					<h2 className="text-xl font-bold">History</h2>
					<span className="text-xs text-muted-foreground">
						from the durable store — survives a restart
					</span>
					{correlationId && (
						<span className="text-xs text-muted-foreground">
							· correlation <code className="font-mono">{correlationId}</code>{" "}
							<Link href="/jobs" className="text-sky-500 hover:underline">
								clear
							</Link>
						</span>
					)}
				</div>
				{history.loading && !history.data && (
					<p className="text-sm text-muted-foreground">Loading…</p>
				)}
				{history.error && (
					<p className="text-sm text-destructive">{history.error}</p>
				)}
				{history.data && past.length === 0 && (
					<p className="text-sm text-muted-foreground">
						No past runs recorded. History needs a durable store — see{" "}
						<code>storage.runtime</code> in workspace.yaml.
					</p>
				)}
				{past.length > 0 && (
					<div className="overflow-hidden rounded-lg border">
						<table className="w-full text-sm">
							<thead>
								<tr className="bg-muted text-muted-foreground">
									<th className="px-4 py-2 text-left font-medium">Job ID</th>
									<th className="px-4 py-2 text-left font-medium">Topology</th>
									<th className="px-4 py-2 text-left font-medium">
										Correlation
									</th>
									<th className="px-4 py-2 text-left font-medium">Version</th>
									<th className="px-4 py-2 text-left font-medium">Status</th>
									<th className="px-4 py-2 text-left font-medium">Started</th>
									<th className="px-4 py-2 text-left font-medium">Completed</th>
									<th className="px-4 py-2 text-left font-medium">
										Tokens in / out
									</th>
									<th className="px-4 py-2 text-left font-medium">Cost</th>
								</tr>
							</thead>
							<tbody>
								{past.map((job) => (
									<tr
										key={job.job_id}
										className="border-t transition-colors hover:bg-muted/50"
									>
										<Cell>
											<JobIdLink id={job.job_id} />
										</Cell>
										<Cell>{job.topology}</Cell>
										<Cell muted>
											<CorrelationLink id={job.correlation_id} />
										</Cell>
										<Cell muted>{job.version ? `v${job.version}` : "-"}</Cell>
										<Cell>
											<StatusBadge status={job.status} />
										</Cell>
										<Cell muted>{formatWhen(job.created_at)}</Cell>
										<Cell muted>{formatWhen(job.completed_at)}</Cell>
										<Cell muted>
											{formatTokens(
												job.usage_input_tokens,
												job.usage_output_tokens,
											)}
										</Cell>
										<Cell muted>{formatCost(job.usage_cost_usd)}</Cell>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				)}
			</section>
		</div>
	);
}

// useSearchParams must sit under a Suspense boundary for the static export prerender.
export default function JobsPage() {
	return (
		<Suspense
			fallback={<p className="text-sm text-muted-foreground">Loading…</p>}
		>
			<JobsView />
		</Suspense>
	);
}
