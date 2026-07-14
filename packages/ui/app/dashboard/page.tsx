"use client";

import { useCallback } from "react";

import { Card, CardTitle } from "@/components/card";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type {
	CanaryStatus,
	HealthResponse,
	JobListItem,
	ValidateResponse,
} from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

function HealthCard() {
	const fetchHealth = useCallback(() => api.health(), []);
	const { data, error, loading } = usePoll<HealthResponse>(fetchHealth, 10000);

	return (
		<Card>
			<CardTitle>Health</CardTitle>
			{loading && <p className="text-sm text-muted-foreground">Connecting…</p>}
			{error && (
				<div className="flex items-center gap-2">
					<span className="size-2 rounded-full bg-destructive" />
					<span className="text-sm text-destructive">Offline</span>
				</div>
			)}
			{data && (
				<div className="flex items-center gap-2">
					<span className="size-2 rounded-full bg-success" />
					<span className="text-sm font-medium">Online</span>
					<span className="ml-auto text-xs text-muted-foreground">
						{data.workspace}
					</span>
				</div>
			)}
		</Card>
	);
}

function ValidationCard() {
	const fetchValidation = useCallback(() => api.validate(), []);
	const { data } = usePoll<ValidateResponse>(fetchValidation, 30000);

	if (!data) return null;
	return (
		<Card>
			<CardTitle>Workspace</CardTitle>
			<div className="grid grid-cols-3 gap-3 text-center">
				<div>
					<p className="text-2xl font-bold">{data.topologies.length}</p>
					<p className="text-xs text-muted-foreground">topologies</p>
				</div>
				<div>
					<p className="text-2xl font-bold">{data.skills.length}</p>
					<p className="text-xs text-muted-foreground">skills</p>
				</div>
				<div>
					<p className="text-2xl font-bold">{data.archetypes.length}</p>
					<p className="text-xs text-muted-foreground">archetypes</p>
				</div>
			</div>
		</Card>
	);
}

function RecentJobs() {
	const fetchJobs = useCallback(() => api.jobs(), []);
	const { data } = usePoll<JobListItem[]>(fetchJobs, 5000);

	const jobs = data?.slice(-5).reverse() ?? [];
	const running = data?.filter((j) => j.status === "running").length ?? 0;
	const completed = data?.filter((j) => j.status === "completed").length ?? 0;
	const failed = data?.filter((j) => j.status === "failed").length ?? 0;

	return (
		<Card className="col-span-2">
			<CardTitle>Recent Jobs</CardTitle>
			<div className="mb-3 flex gap-4 text-sm">
				<span>
					<span className="font-bold">{running}</span>{" "}
					<span className="text-muted-foreground">running</span>
				</span>
				<span>
					<span className="font-bold">{completed}</span>{" "}
					<span className="text-muted-foreground">completed</span>
				</span>
				<span>
					<span className="font-bold">{failed}</span>{" "}
					<span className="text-muted-foreground">failed</span>
				</span>
			</div>
			{jobs.length === 0 ? (
				<p className="text-sm text-muted-foreground">No jobs yet</p>
			) : (
				<div className="space-y-2">
					{jobs.map((job) => (
						<div
							key={job.job_id}
							className="flex items-center gap-3 rounded-md bg-muted px-2 py-1.5 text-sm"
						>
							<StatusBadge status={job.status} />
							<span className="font-mono text-xs">{job.job_id}</span>
							<span className="text-muted-foreground">{job.topology}</span>
							{job.version && <Badge variant="secondary">v{job.version}</Badge>}
							<span className="ml-auto text-xs text-muted-foreground">
								{new Date(job.created_at).toLocaleTimeString()}
							</span>
						</div>
					))}
				</div>
			)}
		</Card>
	);
}

function CanarySummary() {
	const fetchCanary = useCallback(() => api.canary(), []);
	const { data } = usePoll<CanaryStatus>(fetchCanary, 10000);

	if (!data || !data.enabled) {
		return (
			<Card>
				<CardTitle>Canary</CardTitle>
				<p className="text-sm text-muted-foreground">
					No canary routes configured
				</p>
			</Card>
		);
	}

	return (
		<Card>
			<CardTitle>Canary</CardTitle>
			<div className="space-y-3">
				{data.routes.map((route) => (
					<div key={route.topology}>
						<p className="mb-1 text-sm font-medium">{route.topology}</p>
						<div className="flex gap-2">
							{route.versions.map((v) => (
								<div
									key={v.version}
									className="rounded-md border bg-background px-2 py-1 text-xs"
								>
									v{v.version} <span className="font-bold">{v.weight}%</span>
								</div>
							))}
						</div>
					</div>
				))}
			</div>
		</Card>
	);
}

export default function DashboardPage() {
	return (
		<div>
			<h2 className="mb-4 text-xl font-bold">Dashboard</h2>
			<div className="grid grid-cols-3 gap-4">
				<HealthCard />
				<ValidationCard />
				<CanarySummary />
				<RecentJobs />
			</div>
		</div>
	);
}
