"use client";

import { useCallback } from "react";

import { api } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

export default function AuditPage() {
	const fetchAudit = useCallback(() => api.audit({ limit: 200 }), []);
	const { data, error, loading } = usePoll<AuditEvent[]>(fetchAudit, 5000);

	return (
		<div>
			<h2 className="mb-4 text-xl font-bold">Audit log</h2>
			<p className="mb-4 text-sm text-muted-foreground">
				Append-only, newest first. Read-only — the media pillar exposes no edit
				or delete.
			</p>

			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}
			{data && data.length === 0 && (
				<p className="text-sm text-muted-foreground">No audit events yet.</p>
			)}

			{data && data.length > 0 && (
				<div className="overflow-hidden rounded-lg border">
					<table className="w-full text-sm">
						<thead>
							<tr className="bg-muted text-muted-foreground">
								<th className="px-4 py-2 text-left font-medium">Event</th>
								<th className="px-4 py-2 text-left font-medium">Agent</th>
								<th className="px-4 py-2 text-left font-medium">Topology</th>
								<th className="px-4 py-2 text-left font-medium">Run</th>
								<th className="px-4 py-2 text-left font-medium">Time</th>
							</tr>
						</thead>
						<tbody>
							{data.map((event) => (
								<tr key={event.event_id} className="border-t">
									<td className="px-4 py-2 font-mono text-xs">
										{event.event_type}
									</td>
									<td className="px-4 py-2">
										{event.agent_id}
										{event.agent_role ? (
											<span className="text-muted-foreground">
												{" "}
												({event.agent_role})
											</span>
										) : null}
									</td>
									<td className="px-4 py-2 text-xs text-muted-foreground">
										{event.topology_id ?? "-"}
									</td>
									<td className="px-4 py-2 font-mono text-xs text-muted-foreground">
										{event.run_id ? event.run_id.slice(0, 12) : "-"}
									</td>
									<td className="px-4 py-2 text-xs text-muted-foreground">
										{event.timestamp
											? new Date(event.timestamp).toLocaleString()
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
