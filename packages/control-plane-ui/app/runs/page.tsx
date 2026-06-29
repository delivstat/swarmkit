"use client";

import { RefreshCw } from "lucide-react";
import { useCallback } from "react";

import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
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
import type { AuditRow, UsageRow } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

function num(n: number) {
	return n.toLocaleString();
}

function UsageTable() {
	const fetcher = useCallback(() => api.usage(), []);
	const { data, loading } = usePoll<UsageRow[]>(fetcher);
	const rows = data ?? [];

	return (
		<Card>
			<CardHeader>
				<CardTitle>Usage by model</CardTitle>
			</CardHeader>
			<CardContent className="p-0">
				{loading ? (
					<p className="p-6 text-sm text-muted-foreground">Loading…</p>
				) : rows.length === 0 ? (
					<p className="p-6 text-sm text-muted-foreground">
						No usage recorded yet (push to{" "}
						<code className="rounded bg-muted px-1 py-0.5 text-xs">
							POST /aggregate/usage
						</code>
						). Cost is blank until model providers report it.
					</p>
				) : (
					<Table>
						<TableHeader>
							<TableRow>
								<TableHead>Model</TableHead>
								<TableHead>Provider</TableHead>
								<TableHead className="text-right">Input</TableHead>
								<TableHead className="text-right">Output</TableHead>
								<TableHead className="text-right">Cost (USD)</TableHead>
								<TableHead className="text-right">Records</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{rows.map((r) => (
								<TableRow key={`${r.model}:${r.provider}`}>
									<TableCell className="font-medium">
										{r.model ?? "—"}
									</TableCell>
									<TableCell className="text-muted-foreground">
										{r.provider ?? "—"}
									</TableCell>
									<TableCell className="text-right tabular-nums">
										{num(r.input_tokens)}
									</TableCell>
									<TableCell className="text-right tabular-nums">
										{num(r.output_tokens)}
									</TableCell>
									<TableCell className="text-right tabular-nums">
										{r.cost_usd ? `$${r.cost_usd.toFixed(2)}` : "—"}
									</TableCell>
									<TableCell className="text-right tabular-nums">
										{r.records}
									</TableCell>
								</TableRow>
							))}
						</TableBody>
					</Table>
				)}
			</CardContent>
		</Card>
	);
}

function ActivityTable() {
	const fetcher = useCallback(() => api.audit(50), []);
	const { data, loading } = usePoll<AuditRow[]>(fetcher);
	const rows = data ?? [];

	return (
		<Card>
			<CardHeader>
				<CardTitle>Recent activity</CardTitle>
			</CardHeader>
			<CardContent className="p-0">
				{loading ? (
					<p className="p-6 text-sm text-muted-foreground">Loading…</p>
				) : rows.length === 0 ? (
					<p className="p-6 text-sm text-muted-foreground">
						No audit events yet (push to{" "}
						<code className="rounded bg-muted px-1 py-0.5 text-xs">
							POST /aggregate/audit
						</code>
						).
					</p>
				) : (
					<Table>
						<TableHeader>
							<TableRow>
								<TableHead>When</TableHead>
								<TableHead>Instance</TableHead>
								<TableHead>Action</TableHead>
								<TableHead>Details</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{rows.map((r, i) => {
								const { instance_id, ts, action, id, ...rest } = r;
								return (
									<TableRow key={String(id ?? i)}>
										<TableCell className="text-xs text-muted-foreground">
											{ts ?? "—"}
										</TableCell>
										<TableCell className="font-mono text-xs">
											{instance_id}
										</TableCell>
										<TableCell className="font-medium">
											{action ?? "—"}
										</TableCell>
										<TableCell className="max-w-md truncate font-mono text-xs text-muted-foreground">
											{Object.keys(rest).length ? JSON.stringify(rest) : "—"}
										</TableCell>
									</TableRow>
								);
							})}
						</TableBody>
					</Table>
				)}
			</CardContent>
		</Card>
	);
}

export default function RunsPage() {
	// A single refresh re-mounts both tables' pollers via the key bump.
	const fetcher = useCallback(() => api.usage(), []);
	const { refresh } = usePoll<UsageRow[]>(fetcher, 60_000);

	return (
		<>
			<PageHeader
				title="Runs"
				description="Fleet-wide usage and recent activity, aggregated from instance pushes."
				actions={
					<Button variant="outline" size="sm" onClick={refresh}>
						<RefreshCw />
						Refresh
					</Button>
				}
			/>
			<div className="space-y-6 p-6">
				<UsageTable />
				<ActivityTable />
			</div>
		</>
	);
}
