"use client";

import type { ActivityDetail, ActivityEntry } from "@/lib/activity";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

async function fetchActivity(): Promise<{
	entries: ActivityEntry[];
	nextCursor: string | null;
}> {
	const res = await fetch("/api/activity", { cache: "no-store" });
	if (!res.ok) throw new Error(`HTTP ${res.status}`);
	return (await res.json()) as {
		entries: ActivityEntry[];
		nextCursor: string | null;
	};
}

export function ActivityView() {
	const { data, isLoading, error } = useQuery({
		queryKey: ["activity"],
		queryFn: fetchActivity,
	});
	const [openId, setOpenId] = useState<string | null>(null);

	return (
		<main className="mx-auto max-w-screen-md px-4 py-6 md:py-10">
			<Link
				href="/"
				className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
			>
				<span aria-hidden>←</span> Back to brief
			</Link>
			<header className="mt-4">
				<h1 className="text-xl font-semibold tracking-tight md:text-2xl">
					Activity
				</h1>
				<p className="mt-1 text-sm text-muted-foreground">
					Recent DOT runs. For the full audit trail, open the{" "}
					<a
						className="underline underline-offset-2 hover:text-foreground"
						href={runtimePortalUrl()}
						target="_blank"
						rel="noreferrer"
					>
						SwarmKit portal
					</a>
					.
				</p>
			</header>
			{error && (
				<p className="mt-6 text-sm text-destructive">Couldn't load activity.</p>
			)}
			{isLoading && !data && (
				<div className="mt-6 space-y-2">
					{[0, 1, 2].map((i) => (
						<div key={i} className="h-16 animate-pulse rounded bg-muted" />
					))}
				</div>
			)}
			{data && data.entries.length === 0 && (
				<p className="mt-6 text-sm text-muted-foreground">
					No runs yet. Trigger one from the brief.
				</p>
			)}
			<ol className="mt-6 space-y-2">
				{data?.entries.map((e) => (
					<li key={e.runId}>
						<RunRow
							entry={e}
							isOpen={openId === e.runId}
							onToggle={() =>
								setOpenId((cur) => (cur === e.runId ? null : e.runId))
							}
						/>
					</li>
				))}
			</ol>
		</main>
	);
}

function RunRow({
	entry,
	isOpen,
	onToggle,
}: {
	entry: ActivityEntry;
	isOpen: boolean;
	onToggle: () => void;
}) {
	return (
		<article className="rounded-lg border border-border bg-card p-4 shadow-sm">
			<button
				type="button"
				onClick={onToggle}
				className="flex w-full flex-wrap items-center justify-between gap-3 text-left"
				aria-expanded={isOpen}
			>
				<div className="min-w-0">
					<p className="truncate text-sm font-medium">{entry.summary}</p>
					<p className="mt-1 text-xs text-muted-foreground">
						{entry.topologyId} · {new Date(entry.startedAt).toLocaleString()} ·{" "}
						{formatElapsed(entry.elapsedMs)}
					</p>
				</div>
				<StatusBadge status={entry.status} />
			</button>
			{isOpen && <RunDetail runId={entry.runId} />}
		</article>
	);
}

function RunDetail({ runId }: { runId: string }) {
	const { data, isLoading, error } = useQuery({
		queryKey: ["activity", runId],
		queryFn: async (): Promise<ActivityDetail> => {
			const res = await fetch(`/api/activity/${encodeURIComponent(runId)}`, {
				cache: "no-store",
			});
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			return (await res.json()) as ActivityDetail;
		},
	});
	if (isLoading)
		return <p className="mt-3 text-xs text-muted-foreground">Loading…</p>;
	if (error)
		return (
			<p className="mt-3 text-xs text-destructive">Couldn't load detail.</p>
		);
	if (!data) return null;
	return (
		<div className="mt-3 space-y-3 border-t border-border pt-3">
			<div>
				<h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
					Archetypes
				</h4>
				<ul className="mt-1 space-y-1 text-sm">
					{data.archetypes.map((a) => (
						<li key={a.id} className="flex items-center justify-between">
							<span>{a.id}</span>
							<span className="text-xs text-muted-foreground">
								{formatElapsed(a.elapsedMs)}
							</span>
						</li>
					))}
				</ul>
			</div>
			{data.errors.length > 0 && (
				<div>
					<h4 className="text-xs font-medium uppercase tracking-wide text-destructive">
						Errors
					</h4>
					<ul className="mt-1 space-y-1 text-sm text-destructive">
						{data.errors.map((e, i) => (
							<li key={`${e.archetype}-${i}`}>
								<span className="font-medium">{e.archetype}:</span> {e.message}
							</li>
						))}
					</ul>
				</div>
			)}
		</div>
	);
}

function StatusBadge({ status }: { status: ActivityEntry["status"] }) {
	const cls =
		status === "success"
			? "border-emerald-500/40 bg-emerald-500/10 text-emerald-500"
			: status === "error"
				? "border-destructive/40 bg-destructive/10 text-destructive"
				: "border-border bg-muted text-muted-foreground";
	return (
		<span
			className={`inline-flex rounded-md border px-2 py-0.5 text-xs font-medium ${cls}`}
		>
			{status}
		</span>
	);
}

function formatElapsed(ms: number): string {
	if (ms < 1000) return `${ms} ms`;
	const s = ms / 1000;
	if (s < 60) return `${s.toFixed(1)} s`;
	return `${(s / 60).toFixed(1)} min`;
}

function runtimePortalUrl(): string {
	// The runtime hosts the portal at the same origin (see project_serve_hosted_webui). This is a
	// best-effort link when SWARMKIT_RUNTIME_URL isn't reachable from the browser it renders in;
	// commit 10 (deployment) will wire this from server env at render time.
	return "http://localhost:8000/ui/activity";
}
