"use client";

import { ConnectionRow } from "@/components/connection-row";
import type { ConnectionRow as Row } from "@/lib/connections";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

async function fetchConnections(): Promise<{ rows: Row[] }> {
	const res = await fetch("/api/connections", { cache: "no-store" });
	if (!res.ok) throw new Error(`HTTP ${res.status}`);
	return (await res.json()) as { rows: Row[] };
}

export function ConnectionsView({
	justConnected,
}: { justConnected: string | null }) {
	const qc = useQueryClient();
	const { data, isLoading, error } = useQuery({
		queryKey: ["connections"],
		queryFn: fetchConnections,
	});

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
					Connections
				</h1>
				<p className="mt-1 text-sm text-muted-foreground">
					Gmail + Calendar OAuth is handled by the SwarmKit runtime — DOT never
					sees a Google token. Sign-in stays on this browser tab.
				</p>
			</header>
			{justConnected && (
				<output className="mt-4 block rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-500">
					{humanProvider(justConnected)} connected.
				</output>
			)}
			<div className="mt-6 space-y-3">
				{error && (
					<p className="text-sm text-destructive">Couldn't load connections.</p>
				)}
				{isLoading && !data && (
					<>
						<div className="h-20 animate-pulse rounded-lg bg-muted" />
						<div className="h-20 animate-pulse rounded-lg bg-muted" />
					</>
				)}
				{data?.rows.map((row) => (
					<ConnectionRow
						key={row.provider}
						row={row}
						onChanged={() =>
							qc.invalidateQueries({ queryKey: ["connections"] })
						}
					/>
				))}
			</div>
		</main>
	);
}

function humanProvider(id: string): string {
	if (id === "gmail") return "Gmail";
	if (id === "google-calendar") return "Google Calendar";
	return id;
}
