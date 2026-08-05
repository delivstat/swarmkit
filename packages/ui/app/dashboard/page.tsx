"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import {
	bySource,
	byTopology,
	formatCount,
	formatRate,
	formatSpend,
	recentFailures,
	sourceLabel,
	summarize,
	withinHours,
} from "@/lib/dashboard";
import type {
	HealthResponse,
	PersistedJob,
	ReviewGate,
	ValidateResponse,
} from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

/**
 * What the workspace is actually doing.
 *
 * This page used to read `/jobs` — the in-memory store, holding only what the current serve
 * process started via `POST /run/{topology}`, emptied on restart. A workspace driven from the CLI,
 * a pipeline or chat showed an empty dashboard; a restart erased whatever was there. The numbers
 * were not stale, the source was wrong.
 *
 * Everything below reads `/jobs/history`, which is durable and covers every run path, plus
 * `/review` for what is waiting on a human. Counts are windowed rather than all-time, because
 * "142 runs" since the beginning of the workspace is a fact nobody acts on.
 */

const WINDOWS = [
	{ label: "24h", hours: 24 },
	{ label: "7d", hours: 24 * 7 },
	{ label: "30d", hours: 24 * 30 },
] as const;

function Stat({
	value,
	label,
	tone,
}: {
	value: string;
	label: string;
	tone?: "danger" | "muted";
}) {
	return (
		<div>
			<p
				className={`text-2xl font-bold ${tone === "danger" ? "text-destructive" : ""} ${
					tone === "muted" ? "text-muted-foreground" : ""
				}`}
			>
				{value}
			</p>
			<p className="text-xs text-muted-foreground">{label}</p>
		</div>
	);
}

function JobLink({ id }: { id: string }) {
	return (
		<Link
			href={`/job?id=${encodeURIComponent(id)}`}
			className="font-mono text-xs text-sky-500 hover:underline"
		>
			{id}
		</Link>
	);
}

export default function DashboardPage() {
	const [hours, setHours] = useState<number>(24);

	const fetchHealth = useCallback(() => api.health(), []);
	const fetchValidation = useCallback(() => api.validate(), []);
	const fetchHistory = useCallback(() => api.jobsHistory(), []);
	const fetchGates = useCallback(() => api.reviewPending(), []);

	const health = usePoll<HealthResponse>(fetchHealth, 10000);
	const validation = usePoll<ValidateResponse>(fetchValidation, 30000);
	const history = usePoll<PersistedJob[]>(fetchHistory, 10000);
	const gates = usePoll<ReviewGate[]>(fetchGates, 10000);

	const all = useMemo(() => history.data ?? [], [history.data]);
	// Evaluated per render rather than memoised on a clock: the window is relative to now, and a
	// dashboard that freezes its own idea of "now" drifts quietly for as long as the tab is open.
	const windowed = useMemo(
		() => withinHours(all, hours, Date.now()),
		[all, hours],
	);
	const stats = useMemo(() => summarize(windowed), [windowed]);
	const sources = useMemo(() => bySource(windowed), [windowed]);
	const topologies = useMemo(() => byTopology(windowed), [windowed]);
	const failures = useMemo(() => recentFailures(windowed), [windowed]);
	const pendingGates = gates.data ?? [];

	return (
		<div className="space-y-4">
			<div className="flex items-baseline gap-4">
				<h2 className="text-xl font-bold">Dashboard</h2>
				<div className="flex gap-1">
					{WINDOWS.map((w) => (
						<button
							key={w.label}
							type="button"
							onClick={() => setHours(w.hours)}
							className={`rounded-md px-2 py-0.5 text-xs ${
								hours === w.hours
									? "bg-muted font-medium"
									: "text-muted-foreground hover:bg-muted/50"
							}`}
						>
							{w.label}
						</button>
					))}
				</div>
				<div className="ml-auto flex items-center gap-2 text-xs">
					{health.error ? (
						<>
							<span className="size-2 rounded-full bg-destructive" />
							<span className="text-destructive">Offline</span>
						</>
					) : (
						<>
							<span className="size-2 rounded-full bg-success" />
							<span className="text-muted-foreground">
								{health.data?.workspace ?? "connecting…"}
							</span>
						</>
					)}
				</div>
			</div>

			{/* What happened, in the chosen window. */}
			<Card>
				<CardTitle>
					Activity — last {WINDOWS.find((w) => w.hours === hours)?.label}
				</CardTitle>
				{history.loading && !history.data ? (
					<p className="text-sm text-muted-foreground">Loading…</p>
				) : stats.total === 0 ? (
					<p className="text-sm text-muted-foreground">
						No runs in this window. History needs a durable store — see{" "}
						<code>storage.runtime</code> in workspace.yaml.
					</p>
				) : (
					<div className="grid grid-cols-6 gap-3 text-center">
						<Stat value={`${stats.total}`} label="runs" />
						<Stat value={`${stats.running}`} label="in flight" />
						<Stat
							value={`${stats.failed}`}
							label="failed"
							tone={stats.failed > 0 ? "danger" : "muted"}
						/>
						<Stat
							value={formatRate(stats.failureRate)}
							label="failure rate"
							tone={
								stats.failureRate !== null && stats.failureRate > 0.2
									? "danger"
									: undefined
							}
						/>
						<Stat
							value={formatSpend(stats.costUsd, stats.total)}
							label="spend"
						/>
						<Stat
							value={`${formatCount(stats.inputTokens)} / ${formatCount(stats.outputTokens)}`}
							label="tokens in / out"
						/>
					</div>
				)}
			</Card>

			<div className="grid grid-cols-3 gap-4">
				{/* Anything blocked on a person belongs at the top of a dashboard, not behind a tab. */}
				<Card>
					<CardTitle>Waiting on you</CardTitle>
					{pendingGates.length === 0 ? (
						<p className="text-sm text-muted-foreground">
							Nothing awaiting approval.
						</p>
					) : (
						<div className="space-y-2">
							{pendingGates.slice(0, 5).map((gate) => (
								<Link
									key={gate.id}
									href={`/gates?id=${encodeURIComponent(gate.id)}`}
									className="block rounded-md bg-muted px-2 py-1.5 text-sm hover:bg-muted/70"
								>
									<span className="font-medium">{gate.gate_id ?? gate.id}</span>
									{gate.kind && (
										<span className="ml-2 text-xs text-muted-foreground">
											{gate.kind}
										</span>
									)}
								</Link>
							))}
							{pendingGates.length > 5 && (
								<Link
									href="/gates"
									className="block text-xs text-sky-500 hover:underline"
								>
									{pendingGates.length - 5} more…
								</Link>
							)}
						</div>
					)}
				</Card>

				{/* Where the work comes from — unanswerable until `source` existed. */}
				<Card>
					<CardTitle>Where runs come from</CardTitle>
					{sources.length === 0 ? (
						<p className="text-sm text-muted-foreground">
							No runs in this window.
						</p>
					) : (
						<div className="space-y-1.5">
							{sources.map((row) => (
								<div
									key={row.source ?? "unknown"}
									className="flex items-center gap-2 text-sm"
								>
									<span className={row.source ? "" : "text-muted-foreground"}>
										{sourceLabel(row.source)}
									</span>
									<span className="ml-auto text-xs text-muted-foreground">
										{row.runs} {row.runs === 1 ? "run" : "runs"}
									</span>
									<span className="w-16 text-right text-xs">
										{formatSpend(row.costUsd, row.runs)}
									</span>
								</div>
							))}
						</div>
					)}
				</Card>

				{/* The actionable form of a cost total: which swarm is spending it. */}
				<Card>
					<CardTitle>Spend by topology</CardTitle>
					{topologies.length === 0 ? (
						<p className="text-sm text-muted-foreground">
							No runs in this window.
						</p>
					) : (
						<div className="space-y-1.5">
							{topologies.map((row) => (
								<div
									key={row.topology}
									className="flex items-center gap-2 text-sm"
								>
									<span className="truncate">{row.topology}</span>
									{row.failed > 0 && (
										<span className="text-xs text-destructive">
											{row.failed} failed
										</span>
									)}
									<span className="ml-auto text-xs text-muted-foreground">
										{row.runs}
									</span>
									<span className="w-16 text-right text-xs">
										{formatSpend(row.costUsd, row.runs)}
									</span>
								</div>
							))}
						</div>
					)}
				</Card>
			</div>

			{/* Failures surfaced without being asked for. */}
			<Card>
				<CardTitle>Recent failures</CardTitle>
				{failures.length === 0 ? (
					<p className="text-sm text-muted-foreground">
						No failed runs in this window.
					</p>
				) : (
					<div className="space-y-2">
						{failures.map((job) => (
							<div
								key={job.job_id}
								className="flex items-center gap-3 rounded-md bg-muted px-2 py-1.5 text-sm"
							>
								<StatusBadge status={job.status} />
								<JobLink id={job.job_id} />
								<span className="text-muted-foreground">{job.topology}</span>
								{job.source && (
									<span className="text-xs text-muted-foreground">
										{sourceLabel(job.source as never)}
									</span>
								)}
								<span className="ml-auto text-xs text-muted-foreground">
									{new Date(job.created_at).toLocaleString()}
								</span>
							</div>
						))}
					</div>
				)}
			</Card>

			<Card>
				<CardTitle>Workspace</CardTitle>
				<div className="grid grid-cols-3 gap-3 text-center">
					<Stat
						value={`${validation.data?.topologies.length ?? "-"}`}
						label="topologies"
					/>
					<Stat
						value={`${validation.data?.skills.length ?? "-"}`}
						label="skills"
					/>
					<Stat
						value={`${validation.data?.archetypes.length ?? "-"}`}
						label="archetypes"
					/>
				</div>
			</Card>
		</div>
	);
}
