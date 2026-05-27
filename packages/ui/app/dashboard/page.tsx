"use client";

import { Card, CardTitle } from "@/components/card";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import type {
	CanaryStatus,
	HealthResponse,
	JobListItem,
	ValidateResponse,
} from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useCallback } from "react";

function HealthCard() {
	const fetchHealth = useCallback(() => api.health(), []);
	const { data, error, loading } = usePoll<HealthResponse>(fetchHealth, 10000);

	return (
		<Card>
			<CardTitle>Health</CardTitle>
			{loading && <p className="text-sm opacity-50">Connecting...</p>}
			{error && (
				<div className="flex items-center gap-2">
					<span
						className="w-2 h-2 rounded-full"
						style={{ background: "var(--error)" }}
					/>
					<span className="text-sm" style={{ color: "var(--error)" }}>
						Offline
					</span>
				</div>
			)}
			{data && (
				<div className="flex items-center gap-2">
					<span
						className="w-2 h-2 rounded-full"
						style={{ background: "var(--success)" }}
					/>
					<span className="text-sm font-medium">Online</span>
					<span
						className="text-xs ml-auto"
						style={{ color: "var(--fg-muted)" }}
					>
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
					<p className="text-xs" style={{ color: "var(--fg-muted)" }}>
						topologies
					</p>
				</div>
				<div>
					<p className="text-2xl font-bold">{data.skills.length}</p>
					<p className="text-xs" style={{ color: "var(--fg-muted)" }}>
						skills
					</p>
				</div>
				<div>
					<p className="text-2xl font-bold">{data.archetypes.length}</p>
					<p className="text-xs" style={{ color: "var(--fg-muted)" }}>
						archetypes
					</p>
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
			<div className="flex gap-4 mb-3 text-sm">
				<span>
					<span className="font-bold">{running}</span>{" "}
					<span style={{ color: "var(--fg-muted)" }}>running</span>
				</span>
				<span>
					<span className="font-bold">{completed}</span>{" "}
					<span style={{ color: "var(--fg-muted)" }}>completed</span>
				</span>
				<span>
					<span className="font-bold">{failed}</span>{" "}
					<span style={{ color: "var(--fg-muted)" }}>failed</span>
				</span>
			</div>
			{jobs.length === 0 ? (
				<p className="text-sm" style={{ color: "var(--fg-muted)" }}>
					No jobs yet
				</p>
			) : (
				<div className="space-y-2">
					{jobs.map((job) => (
						<div
							key={job.job_id}
							className="flex items-center gap-3 text-sm py-1.5 px-2 rounded"
							style={{ background: "var(--bg)" }}
						>
							<StatusBadge status={job.status} />
							<span className="font-mono text-xs">{job.job_id}</span>
							<span style={{ color: "var(--fg-muted)" }}>{job.topology}</span>
							{job.version && (
								<span
									className="text-xs px-1.5 py-0.5 rounded"
									style={{ background: "var(--border)" }}
								>
									v{job.version}
								</span>
							)}
							<span
								className="ml-auto text-xs"
								style={{ color: "var(--fg-muted)" }}
							>
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
				<p className="text-sm" style={{ color: "var(--fg-muted)" }}>
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
						<p className="text-sm font-medium mb-1">{route.topology}</p>
						<div className="flex gap-2">
							{route.versions.map((v) => (
								<div
									key={v.version}
									className="text-xs px-2 py-1 rounded"
									style={{
										background: "var(--bg)",
										border: "1px solid var(--border)",
									}}
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
			<h2 className="text-xl font-bold mb-4">Dashboard</h2>
			<div className="grid grid-cols-3 gap-4">
				<HealthCard />
				<ValidationCard />
				<CanarySummary />
				<RecentJobs />
			</div>
		</div>
	);
}
