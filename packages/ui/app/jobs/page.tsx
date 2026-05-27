"use client";

import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import type { JobListItem } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import Link from "next/link";
import { useCallback } from "react";

export default function JobsPage() {
	const fetchJobs = useCallback(() => api.jobs(), []);
	const { data, error, loading } = usePoll<JobListItem[]>(fetchJobs, 3000);

	return (
		<div>
			<h2 className="text-xl font-bold mb-4">Jobs</h2>
			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{data && data.length === 0 && (
				<p className="text-sm" style={{ color: "var(--fg-muted)" }}>
					No jobs. Submit a run via POST /run/&#123;topology&#125;.
				</p>
			)}
			{data && data.length > 0 && (
				<div
					className="rounded-lg border overflow-hidden"
					style={{ borderColor: "var(--border)" }}
				>
					<table className="w-full text-sm">
						<thead>
							<tr
								style={{
									background: "var(--bg-sidebar)",
									color: "var(--fg-muted)",
								}}
							>
								<th className="text-left px-4 py-2 font-medium">Job ID</th>
								<th className="text-left px-4 py-2 font-medium">Topology</th>
								<th className="text-left px-4 py-2 font-medium">Version</th>
								<th className="text-left px-4 py-2 font-medium">Status</th>
								<th className="text-left px-4 py-2 font-medium">Created</th>
								<th className="text-left px-4 py-2 font-medium">Completed</th>
							</tr>
						</thead>
						<tbody>
							{[...data].reverse().map((job) => (
								<tr
									key={job.job_id}
									className="border-t hover:opacity-80"
									style={{ borderColor: "var(--border)" }}
								>
									<td className="px-4 py-2">
										<Link
											href={`/jobs/${job.job_id}`}
											className="font-mono text-xs hover:underline"
											style={{ color: "var(--accent)" }}
										>
											{job.job_id}
										</Link>
									</td>
									<td className="px-4 py-2">{job.topology}</td>
									<td
										className="px-4 py-2 text-xs"
										style={{ color: "var(--fg-muted)" }}
									>
										{job.version ? `v${job.version}` : "-"}
									</td>
									<td className="px-4 py-2">
										<StatusBadge status={job.status} />
									</td>
									<td
										className="px-4 py-2 text-xs"
										style={{ color: "var(--fg-muted)" }}
									>
										{new Date(job.created_at).toLocaleString()}
									</td>
									<td
										className="px-4 py-2 text-xs"
										style={{ color: "var(--fg-muted)" }}
									>
										{job.completed_at
											? new Date(job.completed_at).toLocaleString()
											: "-"}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
		</div>
	);
}
