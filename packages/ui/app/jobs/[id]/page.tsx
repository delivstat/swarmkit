"use client";

import { Card, CardTitle } from "@/components/card";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import {
	executorBadge,
	formatTokens,
	formatUsd,
	spanCostUsd,
} from "@/lib/format";
import type { JobResponse, JobUsage, TraceSpan } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

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
					<span
						className="text-xs font-normal ml-2"
						style={{ color: "var(--success)" }}
					>
						live
					</span>
				)}
			</CardTitle>
			<div
				className="font-mono text-xs space-y-1 max-h-64 overflow-y-auto p-2 rounded"
				style={{ background: "var(--bg)" }}
			>
				{events.length === 0 && (
					<p style={{ color: "var(--fg-muted)" }}>Waiting for events...</p>
				)}
				{events.map((event) => (
					<div
						key={`${jobId}-${event}`}
						style={{
							color: event.startsWith("[done]")
								? "var(--success)"
								: "var(--fg)",
						}}
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
			<div className="text-xs" style={{ color: "var(--fg-muted)" }}>
				{label}
			</div>
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
			<div className="grid grid-cols-2 gap-3 sm:grid-cols-5 mt-2">
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
								className="flex w-48 shrink-0 items-center gap-1 font-mono"
								style={{
									paddingLeft: depth * 12,
									color: span.error ? "var(--error)" : "var(--fg)",
								}}
								title={span.error ?? span.name}
							>
								<span className="truncate">{span.name}</span>
								{badge && (
									<span
										className="shrink-0 rounded px-1 text-[10px]"
										style={{
											background: "var(--bg)",
											color: "var(--accent)",
											border: "1px solid var(--accent)",
										}}
										title={`executor: ${badge}`}
									>
										{badge}
									</span>
								)}
							</div>
							<div
								className="relative h-4 flex-1 rounded"
								style={{ background: "var(--bg)" }}
							>
								<div
									className="absolute h-4 rounded"
									style={{
										left: `${offset}%`,
										width: `${width}%`,
										background: span.error ? "var(--error)" : "var(--accent)",
									}}
								/>
							</div>
							{cost > 0 && (
								<div
									className="w-14 shrink-0 text-right"
									style={{ color: "var(--fg-muted)" }}
								>
									{formatUsd(cost)}
								</div>
							)}
							<div
								className="w-16 shrink-0 text-right"
								style={{ color: "var(--fg-muted)" }}
							>
								{span.duration_ms}ms
							</div>
						</div>
					);
				})}
			</div>
		</Card>
	);
}

export default function JobDetailPage() {
	const params = useParams();
	const jobId = params.id as string;

	const fetchJob = useCallback(() => api.job(jobId), [jobId]);
	const { data: job, error, loading } = usePoll<JobResponse>(fetchJob, 2000);

	return (
		<div>
			<h2 className="text-xl font-bold mb-4">
				Job{" "}
				<span
					className="font-mono text-base"
					style={{ color: "var(--fg-muted)" }}
				>
					{jobId}
				</span>
			</h2>

			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}

			{job && (
				<div className="grid gap-4">
					<Card>
						<CardTitle>Status</CardTitle>
						<StatusBadge status={job.status} />
					</Card>

					{job.output && (
						<Card>
							<CardTitle>Output</CardTitle>
							<pre
								className="text-sm whitespace-pre-wrap p-3 rounded max-h-96 overflow-y-auto"
								style={{ background: "var(--bg)" }}
							>
								{job.output}
							</pre>
						</Card>
					)}

					{job.error && (
						<Card>
							<CardTitle>Error</CardTitle>
							<pre
								className="text-sm whitespace-pre-wrap p-3 rounded"
								style={{ background: "var(--bg)", color: "var(--error)" }}
							>
								{job.error}
							</pre>
						</Card>
					)}

					<UsageCard jobId={jobId} />

					<TraceWaterfall runId={jobId} />

					<EventStream jobId={jobId} />
				</div>
			)}
		</div>
	);
}
