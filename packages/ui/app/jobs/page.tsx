"use client";

import Link from "next/link";
import { useCallback, useMemo } from "react";

import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import { formatCost, formatTokens, formatWhen, jobSections } from "@/lib/jobs";
import type { JobListItem, PersistedJob } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

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
function PipelineLink({ id }: { id: string | null }) {
	if (!id) return <span className="text-muted-foreground">-</span>;
	return (
		<Link
			href={`/runs?run=${encodeURIComponent(id)}`}
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

export default function JobsPage() {
	const fetchLive = useCallback(() => api.jobs(), []);
	const fetchHistory = useCallback(() => api.jobsHistory(), []);

	const live = usePoll<JobListItem[]>(fetchLive, 3000);
	// Slower: durable rows only change when a job starts or finishes, and this table can be long.
	const history = usePoll<PersistedJob[]>(fetchHistory, 15000);

	const { running, past } = useMemo(
		() => jobSections(live.data ?? null, history.data ?? null),
		[live.data, history.data],
	);

	return (
		<div className="space-y-8">
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
									<th className="px-4 py-2 text-left font-medium">Pipeline</th>
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
											<PipelineLink id={job.correlation_id} />
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
