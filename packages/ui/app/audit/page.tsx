"use client";

import { api } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useCallback } from "react";

export default function AuditPage() {
	const fetchAudit = useCallback(() => api.audit({ limit: 200 }), []);
	const { data, error, loading } = usePoll<AuditEvent[]>(fetchAudit, 5000);

	return (
		<div>
			<h2 className="text-xl font-bold mb-4">Audit log</h2>
			<p className="text-sm mb-4" style={{ color: "var(--fg-muted)" }}>
				Append-only, newest first. Read-only — the media pillar exposes no edit
				or delete.
			</p>

			{loading && <p className="text-sm opacity-50">Loading…</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{data && data.length === 0 && (
				<p className="text-sm" style={{ color: "var(--fg-muted)" }}>
					No audit events yet.
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
								<th className="text-left px-4 py-2 font-medium">Event</th>
								<th className="text-left px-4 py-2 font-medium">Agent</th>
								<th className="text-left px-4 py-2 font-medium">Topology</th>
								<th className="text-left px-4 py-2 font-medium">Run</th>
								<th className="text-left px-4 py-2 font-medium">Time</th>
							</tr>
						</thead>
						<tbody>
							{data.map((event) => (
								<tr
									key={event.event_id}
									className="border-t"
									style={{ borderColor: "var(--border)" }}
								>
									<td className="px-4 py-2 font-mono text-xs">
										{event.event_type}
									</td>
									<td className="px-4 py-2">
										{event.agent_id}
										{event.agent_role ? (
											<span style={{ color: "var(--fg-muted)" }}>
												{" "}
												({event.agent_role})
											</span>
										) : null}
									</td>
									<td
										className="px-4 py-2 text-xs"
										style={{ color: "var(--fg-muted)" }}
									>
										{event.topology_id ?? "-"}
									</td>
									<td
										className="px-4 py-2 font-mono text-xs"
										style={{ color: "var(--fg-muted)" }}
									>
										{event.run_id ? event.run_id.slice(0, 12) : "-"}
									</td>
									<td
										className="px-4 py-2 text-xs"
										style={{ color: "var(--fg-muted)" }}
									>
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
