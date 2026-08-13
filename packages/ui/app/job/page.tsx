"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { CopyablePre } from "@/components/copyable";
import { StatusBadge } from "@/components/status-badge";
import { TopologyCanvas } from "@/components/topology-canvas";
import { Badge } from "@/components/ui/badge";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { sourceLabel } from "@/lib/dashboard";
import {
	type ToolDetail,
	executorBadge,
	formatTokens,
	formatUsd,
	spanCostUsd,
	toolDetail,
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

/** A tool call, opened: its full name, what it was asked, and what it answered.
 *
 * A dialog rather than an inline expansion: a bounded result is up to 2000 characters, and unfolding
 * that into the waterfall pushes every row below it around while a reader is trying to compare
 * timings. It also gives the arguments and the result room to be read.
 */
/** One span's detail: the failure first when it failed, then whatever the span carries.
 *
 * A row used to be clickable ONLY when it was a tool call, so a step that FAILED had no detail at
 * all — the error was a hover tooltip and a red bar, and the actual message was unreachable. The
 * failure is the thing a reader opening a red row wants, so it leads. */
function SpanDialog({
	span,
	onClose,
}: {
	span: TraceSpan;
	onClose: () => void;
}) {
	const tool = toolDetail(span.attributes);
	const attrs = Object.entries(span.attributes).filter(
		// The tool payload has its own rendering below; the rest is the span's own context.
		([k]) => !k.startsWith("swarmkit.tool."),
	);
	return (
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="max-h-[85vh] gap-3 overflow-y-auto sm:max-w-3xl">
				<DialogHeader>
					<DialogTitle className="font-mono text-sm break-all">
						{span.name}
					</DialogTitle>
				</DialogHeader>

				<p className="text-xs text-muted-foreground">
					{span.duration_ms}ms
					{tool &&
						` · ${tool.resultLength.toLocaleString()} characters returned`}
					{tool?.cached && " · cached"}
				</p>

				{span.error && (
					<div>
						<div className="mb-1 text-xs font-medium text-destructive">
							Failure
						</div>
						<CopyablePre
							value={span.error}
							label="the failure"
							className="max-h-[45vh] text-xs"
						/>
					</div>
				)}

				{tool && <ToolCallBody detail={tool} />}

				{attrs.length > 0 && (
					<div>
						<div className="mb-1 text-xs text-muted-foreground">Attributes</div>
						<CopyablePre
							value={JSON.stringify(Object.fromEntries(attrs), null, 2)}
							label="the span attributes"
							className="max-h-[30vh] text-xs"
						/>
					</div>
				)}
			</DialogContent>
		</Dialog>
	);
}

function ToolCallDialog({
	detail,
	onClose,
}: {
	detail: ToolDetail;
	onClose: () => void;
}) {
	return (
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="max-h-[85vh] gap-3 overflow-y-auto sm:max-w-3xl">
				<DialogHeader>
					<DialogTitle className="font-mono text-sm break-all">
						{detail.name}
					</DialogTitle>
				</DialogHeader>

				<p className="text-xs text-muted-foreground">
					{detail.cached && "cached · "}
					{detail.resultLength.toLocaleString()} characters returned
				</p>
				<ToolCallBody detail={detail} />
			</DialogContent>
		</Dialog>
	);
}

/** A tool call's arguments and result — shared by the tool dialog and the span dialog. */
function ToolCallBody({ detail }: { detail: ToolDetail }) {
	const args = Object.keys(detail.arguments).length
		? JSON.stringify(detail.arguments, null, 2)
		: "";
	return (
		<>
			{args && (
				<div>
					<div className="mb-1 text-xs text-muted-foreground">Arguments</div>
					<CopyablePre
						value={args}
						label="the tool arguments"
						className="text-xs"
					/>
				</div>
			)}

			<div>
				<div className="mb-1 text-xs text-muted-foreground">
					Result
					{detail.truncated && " — truncated; the trace keeps a bounded copy"}
				</div>
				{detail.result ? (
					<CopyablePre
						value={detail.result}
						label="the tool result"
						className="max-h-[45vh] text-xs"
					/>
				) : (
					// A trace written before results were recorded, or a genuinely empty
					// answer. Say which, rather than showing an empty box that reads as
					// "the tool returned nothing".
					<p className="text-xs text-muted-foreground">
						{detail.resultLength > 0
							? "Not recorded — this run predates tool-result capture."
							: "The tool returned nothing."}
					</p>
				)}
			</div>
		</>
	);
}

function TraceWaterfall({ runId }: { runId: string }) {
	const fetchTrace = useCallback(() => api.runTrace(runId), [runId]);
	const { data } = usePoll<TraceSpan>(fetchTrace, 5000);
	// The SPAN, not just its tool call: a failed step carries no tool payload, so keying the dialog
	// on the tool made every failure unopenable.
	const [openSpan, setOpenSpan] = useState<TraceSpan | null>(null);
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
					// Openable when there is anything to show: a tool call, a failure, or any
					// attributes the span recorded.
					const tool = toolDetail(span.attributes);
					const openable =
						Boolean(tool) ||
						Boolean(span.error) ||
						Object.keys(span.attributes).length > 0;
					const key = `${span.name}-${span.start_ns}`;
					return (
						<div key={key}>
							<div
								className={cn(
									"flex items-center gap-2 text-xs",
									openable && "cursor-pointer",
								)}
								onClick={openable ? () => setOpenSpan(span) : undefined}
								onKeyDown={
									openable
										? (e) => {
												if (e.key === "Enter" || e.key === " ") {
													e.preventDefault();
													setOpenSpan(span);
												}
											}
										: undefined
								}
								tabIndex={openable ? 0 : undefined}
							>
								<div
									className={cn(
										"flex w-48 shrink-0 items-center gap-1 font-mono",
										span.error && "text-destructive",
									)}
									style={{ paddingLeft: depth * 12 }}
									title={span.error ? `failed: ${span.error}` : span.name}
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
						</div>
					);
				})}
			</div>
			{openSpan && (
				<SpanDialog span={openSpan} onClose={() => setOpenSpan(null)} />
			)}
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

/** What this run WAS: which topology, when, where from, what it cost.
 *
 * The page opened on a status badge and an output blob. Everything here was in the store and
 * dropped by the API, so a reader could see what came back and nothing about the run that produced
 * it — not the topology version, not when it started, not what it was asked.
 */
function RunSummary({ job }: { job: JobResponse }) {
	const started = job.created_at ? new Date(job.created_at) : null;
	const finished = job.completed_at ? new Date(job.completed_at) : null;
	// Elapsed only when both ends are known. A running job has no duration yet, and showing the
	// time since it started as if it were final would misreport it.
	const elapsed =
		started && finished
			? `${Math.max(0, Math.round((finished.getTime() - started.getTime()) / 1000))}s`
			: null;

	return (
		<Card>
			<CardTitle>Run</CardTitle>
			<dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3">
				<Field label="Topology">
					{job.topology ? (
						<Link
							href={`/topologies?id=${encodeURIComponent(job.topology)}`}
							className="text-sky-500 hover:underline"
						>
							{job.topology}
						</Link>
					) : (
						"-"
					)}
				</Field>
				<Field label="Version">{job.version ? `v${job.version}` : "-"}</Field>
				<Field label="Started">
					{started ? started.toLocaleString() : "-"}
				</Field>
				<Field label="Finished">
					{finished ? finished.toLocaleString() : "-"}
				</Field>
				<Field label="Elapsed">{elapsed ?? "-"}</Field>
				<Field label="Source">
					{sourceLabel((job.source ?? null) as never)}
				</Field>
				{job.correlation_id && (
					<Field label="Part of">
						<Link
							href={`/runs?run=${encodeURIComponent(job.correlation_id)}`}
							className="font-mono text-xs text-sky-500 hover:underline"
						>
							{job.correlation_id}
						</Link>
					</Field>
				)}
			</dl>
		</Card>
	);
}

function Field({
	label,
	children,
}: { label: string; children: React.ReactNode }) {
	return (
		<div>
			<dt className="text-xs text-muted-foreground">{label}</dt>
			<dd className="truncate">{children}</dd>
		</div>
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

					<RunSummary job={job} />

					{job.input && (
						<Card>
							<CardTitle>Input</CardTitle>
							{/* What the run was asked. An output is not reviewable without it — and
							    on a pipeline stage this is the resolved input, upstream artifacts
							    and human decisions included. */}
							<CopyablePre
								value={job.input}
								label="the job input"
								className="max-h-64 text-sm"
							/>
						</Card>
					)}

					{job.output && (
						<Card>
							<CardTitle>Output</CardTitle>
							<CopyablePre
								value={job.output}
								label="the job output"
								className="max-h-96 text-sm"
							/>
						</Card>
					)}

					{job.error && (
						<Card>
							<CardTitle>Error</CardTitle>
							<CopyablePre
								value={job.error}
								label="the job error"
								className="text-sm text-destructive"
							/>
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
