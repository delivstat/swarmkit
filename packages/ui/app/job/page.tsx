"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { StatusBadge } from "@/components/status-badge";
import { TopologyCanvas } from "@/components/topology-canvas";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import {
	executorBadge,
	formatTokens,
	formatUsd,
	spanCostUsd,
} from "@/lib/format";
import { traceToOverlay } from "@/lib/topology-run";
import type {
	JobResponse,
	JobUsage,
	TopologyDetail,
	TraceSpan,
} from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { cn } from "@/lib/utils";

function EventStream({ jobId }: { jobId: string }) {
	const [events, setEvents] = useState<string[]>([]);
	const [connected, setConnected] = useState(false);
	const bottomRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		const url = api.jobStreamUrl(jobId);
		const source = new EventSource(url);
		setConnected(true);

		source.onmessage = (e) => {
			setEvents((prev) => [...prev, e.data]);
			if (e.data.startsWith("[done]")) {
				source.close();
				setConnected(false);
			}
		};

		source.onerror = () => {
			source.close();
			setConnected(false);
		};

		return () => source.close();
	}, [jobId]);

	useEffect(() => {
		bottomRef.current?.scrollIntoView({ behavior: "smooth" });
	}, [events.length]);

	return (
		<Card>
			<CardTitle>
				Event Stream{" "}
				{connected && (
					<span className="ml-2 text-xs font-normal text-success">live</span>
				)}
			</CardTitle>
			<div className="max-h-64 space-y-1 overflow-y-auto rounded-md bg-muted p-2 font-mono text-xs">
				{events.length === 0 && (
					<p className="text-muted-foreground">Waiting for events…</p>
				)}
				{events.map((event) => (
					<div
						key={`${jobId}-${event}`}
						className={cn(event.startsWith("[done]") && "text-success")}
					>
						{event}
					</div>
				))}
				<div ref={bottomRef} />
			</div>
		</Card>
	);
}

function Stat({ label, value }: { label: string; value: string }) {
	return (
		<div>
			<div className="text-xs text-muted-foreground">{label}</div>
			<div className="text-sm font-medium">{value}</div>
		</div>
	);
}

function UsageCard({ jobId }: { jobId: string }) {
	const fetchUsage = useCallback(() => api.jobUsage(jobId), [jobId]);
	const { data } = usePoll<JobUsage>(fetchUsage, 3000);
	// Usage is recorded on completion — nothing to show until the run has logged an LLM call.
	if (!data || data.total_calls === 0) return null;
	return (
		<Card>
			<CardTitle>Usage &amp; cost</CardTitle>
			<div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-5">
				<Stat label="Cost" value={formatUsd(data.total_cost_usd)} />
				<Stat label="LLM calls" value={String(data.total_calls)} />
				<Stat label="Input" value={formatTokens(data.total_input_tokens)} />
				<Stat label="Output" value={formatTokens(data.total_output_tokens)} />
				<Stat label="Cache" value={formatTokens(data.total_cache_tokens)} />
			</div>
		</Card>
	);
}

function flattenSpans(
	span: TraceSpan,
	depth = 0,
	out: { span: TraceSpan; depth: number }[] = [],
): { span: TraceSpan; depth: number }[] {
	out.push({ span, depth });
	for (const child of span.children) flattenSpans(child, depth + 1, out);
	return out;
}

function TraceWaterfall({ runId }: { runId: string }) {
	const fetchTrace = useCallback(() => api.runTrace(runId), [runId]);
	const { data } = usePoll<TraceSpan>(fetchTrace, 5000);
	// No trace yet (run unfinished / not recorded) → the endpoint 404s → hide the card.
	if (!data) return null;
	const rootStart = data.start_ns;
	const total = Math.max(1, data.end_ns - data.start_ns);
	return (
		<Card>
			<CardTitle>Trace</CardTitle>
			<div className="mt-2 space-y-1">
				{flattenSpans(data).map(({ span, depth }) => {
					const offset = ((span.start_ns - rootStart) / total) * 100;
					const width = Math.max(
						0.5,
						((span.end_ns - span.start_ns) / total) * 100,
					);
					// A harness node (executor.kind !== "model") gets a chip so it's visually
					// distinct from a model node; both share the same waterfall row (design §5).
					const badge = executorBadge(span.attributes);
					const cost = spanCostUsd(span.attributes);
					return (
						<div
							key={`${span.name}-${span.start_ns}`}
							className="flex items-center gap-2 text-xs"
						>
							<div
								className={cn(
									"flex w-48 shrink-0 items-center gap-1 font-mono",
									span.error && "text-destructive",
								)}
								style={{ paddingLeft: depth * 12 }}
								title={span.error ?? span.name}
							>
								<span className="truncate">{span.name}</span>
								{badge && (
									<Badge
										variant="outline"
										className="shrink-0 px-1 py-0 text-[10px]"
										title={`executor: ${badge}`}
									>
										{badge}
									</Badge>
								)}
							</div>
							<div className="relative h-4 flex-1 rounded bg-muted">
								<div
									className={cn(
										"absolute h-4 rounded",
										span.error ? "bg-destructive" : "bg-sky-500",
									)}
									style={{ left: `${offset}%`, width: `${width}%` }}
								/>
							</div>
							{cost > 0 && (
								<div className="w-14 shrink-0 text-right text-muted-foreground">
									{formatUsd(cost)}
								</div>
							)}
							<div className="w-16 shrink-0 text-right text-muted-foreground">
								{span.duration_ms}ms
							</div>
						</div>
					);
				})}
			</div>
		</Card>
	);
}

/** The run mapped onto the topology graph: which agents fired, their cost/duration/status; nodes
 * that did not fire dim. Polls the trace so it fills in as an in-flight run progresses. */
function RunGraph({ runId, topology }: { runId: string; topology: string }) {
	const [detail, setDetail] = useState<TopologyDetail | null>(null);
	useEffect(() => {
		let live = true;
		api
			.topologyDetail(topology)
			.then((d) => live && setDetail(d))
			.catch(() => live && setDetail(null));
		return () => {
			live = false;
		};
	}, [topology]);

	const fetchTrace = useCallback(() => api.runTrace(runId), [runId]);
	const { data: trace } = usePoll<TraceSpan>(fetchTrace, 5000);
	const overlay = useMemo(() => traceToOverlay(trace ?? null), [trace]);

	if (!detail) return null;
	return (
		<Card>
			<CardTitle>Run graph</CardTitle>
			<p className="mb-2 text-xs text-muted-foreground">
				The run over the topology — green fired, red errored, dimmed did not
				fire.
			</p>
			<div className="h-[440px]">
				<TopologyCanvas root={detail.resolved} overlay={overlay} />
			</div>
		</Card>
	);
}

function JobDetail() {
	const jobId = useSearchParams().get("id") ?? "";

	const fetchJob = useCallback(() => api.job(jobId), [jobId]);
	const { data: job, error, loading } = usePoll<JobResponse>(fetchJob, 2000);

	return (
		<div>
			<h2 className="mb-4 text-xl font-bold">
				Job{" "}
				<span className="font-mono text-base text-muted-foreground">
					{jobId}
				</span>
			</h2>

			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}

			{job && (
				<div className="grid gap-4">
					<Card>
						<CardTitle>Status</CardTitle>
						<StatusBadge status={job.status} />
					</Card>

					{job.output && (
						<Card>
							<CardTitle>Output</CardTitle>
							<pre className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-sm">
								{job.output}
							</pre>
						</Card>
					)}

					{job.error && (
						<Card>
							<CardTitle>Error</CardTitle>
							<pre className="whitespace-pre-wrap rounded-md bg-muted p-3 text-sm text-destructive">
								{job.error}
							</pre>
						</Card>
					)}

					<UsageCard jobId={jobId} />

					{job.topology && <RunGraph runId={jobId} topology={job.topology} />}

					<TraceWaterfall runId={jobId} />

					<EventStream jobId={jobId} />
				</div>
			)}
		</div>
	);
}

// useSearchParams must sit under a Suspense boundary for the static export prerender.
export default function JobPage() {
	return (
		<Suspense
			fallback={<p className="text-sm text-muted-foreground">Loading…</p>}
		>
			<JobDetail />
		</Suspense>
	);
}
