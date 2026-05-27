"use client";

import { Card } from "@/components/card";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import type { TriggerConfig } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useCallback } from "react";

export default function TriggersPage() {
	const fetchTriggers = useCallback(() => api.triggers(), []);
	const { data, error, loading } = usePoll<TriggerConfig[]>(
		fetchTriggers,
		30000,
	);

	return (
		<div>
			<h2 className="text-xl font-bold mb-4">Triggers</h2>
			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{data && data.length === 0 && (
				<Card>
					<p className="text-sm" style={{ color: "var(--fg-muted)" }}>
						No triggers configured.
					</p>
				</Card>
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
								<th className="text-left px-4 py-2 font-medium">ID</th>
								<th className="text-left px-4 py-2 font-medium">Type</th>
								<th className="text-left px-4 py-2 font-medium">Status</th>
								<th className="text-left px-4 py-2 font-medium">Targets</th>
								<th className="text-left px-4 py-2 font-medium">Config</th>
							</tr>
						</thead>
						<tbody>
							{data.map((trigger) => (
								<tr
									key={trigger.id}
									className="border-t"
									style={{ borderColor: "var(--border)" }}
								>
									<td className="px-4 py-2 font-medium">{trigger.id}</td>
									<td className="px-4 py-2">{trigger.type}</td>
									<td className="px-4 py-2">
										<StatusBadge
											status={trigger.enabled ? "completed" : "failed"}
										/>
									</td>
									<td className="px-4 py-2 text-xs font-mono">
										{trigger.targets.join(", ")}
									</td>
									<td
										className="px-4 py-2 text-xs"
										style={{ color: "var(--fg-muted)" }}
									>
										{Object.entries(trigger.config)
											.map(([k, v]) => `${k}=${String(v)}`)
											.join(", ") || "-"}
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
