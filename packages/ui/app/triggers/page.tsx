"use client";

import { useCallback } from "react";

import { Card } from "@/components/card";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import type { TriggerConfig } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

export default function TriggersPage() {
	const fetchTriggers = useCallback(() => api.triggers(), []);
	const { data, error, loading } = usePoll<TriggerConfig[]>(
		fetchTriggers,
		30000,
	);

	return (
		<div>
			<h2 className="mb-4 text-xl font-bold">Triggers</h2>
			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}
			{data && data.length === 0 && (
				<Card>
					<p className="text-sm text-muted-foreground">
						No triggers configured.
					</p>
				</Card>
			)}
			{data && data.length > 0 && (
				<div className="overflow-hidden rounded-lg border">
					<table className="w-full text-sm">
						<thead>
							<tr className="bg-muted text-muted-foreground">
								<th className="px-4 py-2 text-left font-medium">ID</th>
								<th className="px-4 py-2 text-left font-medium">Type</th>
								<th className="px-4 py-2 text-left font-medium">Status</th>
								<th className="px-4 py-2 text-left font-medium">Targets</th>
								<th className="px-4 py-2 text-left font-medium">Config</th>
							</tr>
						</thead>
						<tbody>
							{data.map((trigger) => (
								<tr key={trigger.id} className="border-t">
									<td className="px-4 py-2 font-medium">{trigger.id}</td>
									<td className="px-4 py-2">{trigger.type}</td>
									<td className="px-4 py-2">
										<StatusBadge
											status={trigger.enabled ? "completed" : "failed"}
										/>
									</td>
									<td className="px-4 py-2 font-mono text-xs">
										{trigger.targets.join(", ")}
									</td>
									<td className="px-4 py-2 text-xs text-muted-foreground">
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
