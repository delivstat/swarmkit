"use client";

import { RefreshCw } from "lucide-react";
import { useCallback } from "react";
import { useSWRConfig } from "swr";

import { JaegerLink } from "@/components/jaeger-link";
import { PageHeader } from "@/components/page-header";
import { RunsDetail } from "@/components/runs-detail";
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
import { useInstances } from "@/lib/instance-context";
import { telemetryServiceName } from "@/lib/jaeger";
import type { AuditRow, CachedState, Config, UsageRow } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

function num(n: number) {
	return n.toLocaleString();
}

/** Pushed aggregate (always available, scopeable per instance). `scope` is "" for the whole fleet. */
function UsageTable({ scope }: { scope: string }) {
	const fetcher = useCallback(() => api.usage(scope || undefined), [scope]);
	const { data, loading } = useResource<UsageRow[]>(
		`/usage?instance_id=${scope}`,
		fetcher,
	);
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
						No usage recorded yet (pushed to{" "}
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

function ActivityTable({ scope }: { scope: string }) {
	const fetcher = useCallback(() => api.audit(50, scope || undefined), [scope]);
	const { data, loading } = useResource<AuditRow[]>(
		`/audit?limit=50&instance_id=${scope}`,
		fetcher,
	);
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
						No audit events yet (pushed to{" "}
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
	const { selected, selectedId } = useInstances();
	// One button revalidates every cached resource on the page (all tables share the SWR cache).
	const { mutate } = useSWRConfig();
	const refresh = () => {
		void mutate(() => true);
	};

	// Jaeger deep-link: the panel's configured Jaeger URL + the selected instance's OTel service
	// (its workspace id, from the cached state — falls back to the instance name).
	const { data: config } = useResource<Config>("/config", () => api.config());
	const jaegerUrl = config?.observability?.jaeger_url ?? "";
	const { data: cached } = useResource<CachedState>(
		selectedId ? `/instances/${selectedId}/state` : null,
		() => api.instanceState(selectedId),
	);
	// Mirror the runtime's OTel service.name ("<name> (<id>)") so the deep-link resolves.
	const traceService = cached
		? telemetryServiceName(
				cached.state.workspace_name ?? "",
				cached.state.workspace_id,
			)
		: (selected?.name ?? "");

	const scopeLabel = selected ? selected.name : "all instances";

	return (
		<>
			<PageHeader
				title="Runs"
				description={`Usage + activity for ${scopeLabel}. Aggregates are pushed; per-run detail is fetched live from the instance.`}
				actions={
					<>
						{selected ? (
							<JaegerLink baseUrl={jaegerUrl} service={traceService} />
						) : null}
						<Button variant="outline" size="sm" onClick={refresh}>
							<RefreshCw />
							Refresh
						</Button>
					</>
				}
			/>
			<div className="space-y-6 p-6">
				<UsageTable scope={selectedId} />
				{/* Per-run detail is federated per-instance — only meaningful for one instance. */}
				{selected ? (
					<RunsDetail instanceId={selected.id} instanceName={selected.name} />
				) : (
					<Card>
						<CardHeader>
							<CardTitle>Per-run detail</CardTitle>
						</CardHeader>
						<CardContent>
							<p className="text-sm text-muted-foreground">
								Select a single instance (top-left) to see individual runs with
								per-run cost. Run detail is fetched live from that instance and
								isn’t stored on the panel.
							</p>
						</CardContent>
					</Card>
				)}
				<ActivityTable scope={selectedId} />
			</div>
		</>
	);
}
