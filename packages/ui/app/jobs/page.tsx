"use client";

import Link from "next/link";
import { useCallback } from "react";

import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import type { JobListItem } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

export default function JobsPage() {
	const fetchJobs = useCallback(() => api.jobs(), []);
	const { data, error, loading } = usePoll<JobListItem[]>(fetchJobs, 3000);

	return (
		<div>
			<h2 className="mb-4 text-xl font-bold">Jobs</h2>
			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}
			{data && data.length === 0 && (
				<p className="text-sm text-muted-foreground">
					No jobs. Submit a run via POST /run/&#123;topology&#125;.
				</p>
			)}
			{data && data.length > 0 && (
				<div className="overflow-hidden rounded-lg border">
					<table className="w-full text-sm">
						<thead>
							<tr className="bg-muted text-muted-foreground">
								<th className="px-4 py-2 text-left font-medium">Job ID</th>
								<th className="px-4 py-2 text-left font-medium">Topology</th>
								<th className="px-4 py-2 text-left font-medium">Version</th>
								<th className="px-4 py-2 text-left font-medium">Status</th>
								<th className="px-4 py-2 text-left font-medium">Created</th>
								<th className="px-4 py-2 text-left font-medium">Completed</th>
							</tr>
						</thead>
						<tbody>
							{[...data].reverse().map((job) => (
								<tr
									key={job.job_id}
									className="border-t transition-colors hover:bg-muted/50"
								>
									<td className="px-4 py-2">
										<Link
											href={`/job?id=${job.job_id}`}
											className="font-mono text-xs text-sky-500 hover:underline"
										>
											{job.job_id}
										</Link>
									</td>
									<td className="px-4 py-2">{job.topology}</td>
									<td className="px-4 py-2 text-xs text-muted-foreground">
										{job.version ? `v${job.version}` : "-"}
									</td>
									<td className="px-4 py-2">
										<StatusBadge status={job.status} />
									</td>
									<td className="px-4 py-2 text-xs text-muted-foreground">
										{new Date(job.created_at).toLocaleString()}
									</td>
									<td className="px-4 py-2 text-xs text-muted-foreground">
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
