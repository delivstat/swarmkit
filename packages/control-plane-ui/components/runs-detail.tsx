"use client";

import { useCallback, useMemo, useState } from "react";

import { RunGraph } from "@/components/run-graph";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import type { RunRow, RunsEnvelope } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

function statusVariant(status: string): BadgeProps["variant"] {
	const s = status.toLowerCase();
	if (s === "running" || s === "pending") return "warning";
	if (s === "completed" || s === "succeeded") return "success";
	if (s === "failed" || s === "error" || s === "cancelled")
		return "destructive";
	return "muted";
}

function cost(run: RunRow): string {
	return run.usage_cost_usd != null ? `$${run.usage_cost_usd.toFixed(4)}` : "—";
}

function num(n: number | undefined): string {
	return n != null ? n.toLocaleString() : "—";
}

/** Federated per-run detail for one instance (design 24): live per-run cost/status, pulled on
 * demand and never stored. Searchable, and honest about the three states — reachable+runs,
 * reachable+empty, or unavailable (poll-mode Mode-B / unreachable). */
export function RunsDetail({
	instanceId,
	instanceName,
}: {
	instanceId: string;
	instanceName: string;
}) {
	const fetcher = useCallback(() => api.instanceRuns(instanceId), [instanceId]);
	const { data, error, loading } = useResource<RunsEnvelope>(
		`/instances/${instanceId}/runs`,
		fetcher,
		{ refreshInterval: 5000 },
	);
	const [query, setQuery] = useState("");
	const [graphRun, setGraphRun] = useState<string | null>(null);

	const runs = data?.runs ?? [];
	const filtered = useMemo(() => {
		const q = query.trim().toLowerCase();
		if (!q) return runs;
		return runs.filter((r) =>
			`${r.job_id} ${r.topology} ${r.status}`.toLowerCase().includes(q),
		);
	}, [runs, query]);

	const reachable = data?.reachable ?? true;
	const unavailableMsg =
		data?.reason === "poll-mode"
			? "This is a poll-mode (Mode B) instance — the panel can’t pull live run detail. Only its aggregate cost above is available."
			: "Instance unavailable — the panel couldn’t fetch live run detail right now.";

	return (
		<div className="space-y-4">
			<Card>
				<CardHeader className="flex-row items-center justify-between gap-4 space-y-0">
					<CardTitle>Runs on {instanceName}</CardTitle>
					{reachable && runs.length > 0 ? (
						<input
							aria-label="Search runs"
							placeholder="Search topology, status, id…"
							value={query}
							onChange={(e) => setQuery(e.target.value)}
							className="h-8 w-56 rounded-md border border-input bg-background px-2 text-sm"
						/>
					) : null}
				</CardHeader>
				<CardContent className="p-0">
					{error ? (
						<p className="p-6 text-sm text-muted-foreground">
							Couldn’t reach the control plane: {error}
						</p>
					) : loading ? (
						<p className="p-6 text-sm text-muted-foreground">Loading…</p>
					) : !reachable ? (
						<p className="p-6 text-sm text-muted-foreground">
							{unavailableMsg}
						</p>
					) : runs.length === 0 ? (
						<p className="p-6 text-sm text-muted-foreground">
							No runs recorded on this instance yet.
						</p>
					) : filtered.length === 0 ? (
						<p className="p-6 text-sm text-muted-foreground">
							No runs match “{query}”.
						</p>
					) : (
						<Table>
							<TableHeader>
								<TableRow>
									<TableHead>Topology</TableHead>
									<TableHead>Status</TableHead>
									<TableHead className="text-right">Input</TableHead>
									<TableHead className="text-right">Output</TableHead>
									<TableHead className="text-right">Cost</TableHead>
									<TableHead>When</TableHead>
									<TableHead className="text-right" />
								</TableRow>
							</TableHeader>
							<TableBody>
								{filtered.map((r) => (
									<TableRow key={r.job_id}>
										<TableCell className="font-medium">{r.topology}</TableCell>
										<TableCell>
											<Badge variant={statusVariant(r.status)}>
												{r.status}
											</Badge>
										</TableCell>
										<TableCell className="text-right tabular-nums">
											{num(r.usage_input_tokens)}
										</TableCell>
										<TableCell className="text-right tabular-nums">
											{num(r.usage_output_tokens)}
										</TableCell>
										<TableCell className="text-right tabular-nums">
											{cost(r)}
										</TableCell>
										<TableCell className="text-xs text-muted-foreground">
											{r.completed_at ?? r.created_at ?? "—"}
										</TableCell>
										<TableCell className="text-right">
											<button
												type="button"
												onClick={() =>
													setGraphRun((cur) =>
														cur === r.job_id ? null : r.job_id,
													)
												}
												className="text-xs text-primary hover:underline"
											>
												{graphRun === r.job_id ? "hide graph" : "graph"}
											</button>
										</TableCell>
									</TableRow>
								))}
							</TableBody>
						</Table>
					)}
				</CardContent>
			</Card>
			{graphRun ? (
				<Card>
					<CardHeader className="flex-row items-center justify-between gap-4 space-y-0">
						<CardTitle>Run graph — {graphRun}</CardTitle>
						<button
							type="button"
							onClick={() => setGraphRun(null)}
							className="text-xs text-muted-foreground hover:text-foreground"
						>
							Close
						</button>
					</CardHeader>
					<CardContent className="p-0">
						<RunGraph instanceId={instanceId} runId={graphRun} />
					</CardContent>
				</Card>
			) : null}
		</div>
	);
}
