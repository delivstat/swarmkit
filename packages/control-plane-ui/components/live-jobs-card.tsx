"use client";

import { useCallback } from "react";

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
import type { Job } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

function statusVariant(status: string): BadgeProps["variant"] {
	const s = status.toLowerCase();
	if (s === "running" || s === "pending") return "warning";
	if (s === "completed" || s === "succeeded") return "success";
	if (s === "failed" || s === "error" || s === "cancelled")
		return "destructive";
	return "muted";
}

export function LiveJobsCard({ instanceId }: { instanceId: string }) {
	const fetcher = useCallback(() => api.instanceJobs(instanceId), [instanceId]);
	const { data, error, loading } = useResource<Job[]>(
		`/instances/${instanceId}/jobs`,
		fetcher,
		{ refreshInterval: 5000 },
	);
	const jobs = data ?? [];

	return (
		<Card>
			<CardHeader>
				<CardTitle>Live jobs</CardTitle>
			</CardHeader>
			<CardContent className="p-0">
				{error ? (
					<p className="p-6 text-sm text-muted-foreground">
						Live jobs are queried directly from the instance; this one
						isn&rsquo;t reachable right now ({error}).
					</p>
				) : loading ? (
					<p className="p-6 text-sm text-muted-foreground">Loading…</p>
				) : jobs.length === 0 ? (
					<p className="p-6 text-sm text-muted-foreground">No active jobs.</p>
				) : (
					<Table>
						<TableHeader>
							<TableRow>
								<TableHead>Job</TableHead>
								<TableHead>Topology</TableHead>
								<TableHead>Status</TableHead>
								<TableHead>Created</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{jobs.map((j) => (
								<TableRow key={j.job_id}>
									<TableCell className="font-mono text-xs">
										{j.job_id}
									</TableCell>
									<TableCell className="font-medium">{j.topology}</TableCell>
									<TableCell>
										<Badge variant={statusVariant(j.status)}>{j.status}</Badge>
									</TableCell>
									<TableCell className="text-xs text-muted-foreground">
										{j.created_at}
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
