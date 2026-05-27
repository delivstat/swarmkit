"use client";

import { Card, CardTitle } from "@/components/card";
import { api } from "@/lib/api";
import type { CanaryStatus } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useCallback, useState } from "react";

export default function CanaryPage() {
	const fetchCanary = useCallback(() => api.canary(), []);
	const { data, error, loading, refetch } = usePoll<CanaryStatus>(
		fetchCanary,
		5000,
	);
	const [actionMsg, setActionMsg] = useState<string | null>(null);

	const promote = async (topology: string, version: string) => {
		try {
			await api.canaryPromote(topology, version);
			setActionMsg(`Promoted ${topology} to v${version}`);
			refetch();
		} catch (err) {
			setActionMsg(
				`Error: ${err instanceof Error ? err.message : String(err)}`,
			);
		}
	};

	const rollback = async (topology: string) => {
		try {
			await api.canaryRollback(topology);
			setActionMsg(`Rolled back ${topology}`);
			refetch();
		} catch (err) {
			setActionMsg(
				`Error: ${err instanceof Error ? err.message : String(err)}`,
			);
		}
	};

	return (
		<div>
			<h2 className="text-xl font-bold mb-4">Canary Deployments</h2>
			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{actionMsg && (
				<p
					className="text-sm mb-3 px-3 py-2 rounded"
					style={{ background: "var(--bg-sidebar)" }}
				>
					{actionMsg}
				</p>
			)}
			{data && !data.enabled && (
				<Card>
					<p className="text-sm" style={{ color: "var(--fg-muted)" }}>
						No canary routes configured. Add <code>server.canary.routes</code>{" "}
						to workspace.yaml.
					</p>
				</Card>
			)}
			{data?.routes.map((route) => (
				<Card key={route.topology} className="mb-4">
					<div className="flex items-center justify-between mb-4">
						<CardTitle>{route.topology}</CardTitle>
						<button
							type="button"
							onClick={() => rollback(route.topology)}
							className="text-xs px-2.5 py-1 rounded border"
							style={{ borderColor: "var(--border)", color: "var(--error)" }}
						>
							Rollback
						</button>
					</div>
					<div className="space-y-3">
						{route.versions.map((v) => (
							<div
								key={v.version}
								className="p-3 rounded border"
								style={{
									borderColor: "var(--border)",
									background: "var(--bg)",
								}}
							>
								<div className="flex items-center justify-between mb-2">
									<span className="font-medium">
										v{v.version}{" "}
										<span
											className="text-sm font-bold ml-1"
											style={{
												color:
													v.weight > 0 ? "var(--accent)" : "var(--fg-muted)",
											}}
										>
											{v.weight}%
										</span>
									</span>
									{v.weight < 100 && (
										<button
											type="button"
											onClick={() => promote(route.topology, v.version)}
											className="text-xs px-2.5 py-1 rounded font-medium"
											style={{
												background: "var(--accent)",
												color: "var(--accent-fg)",
											}}
										>
											Promote
										</button>
									)}
								</div>
								{v.metrics && (
									<div className="grid grid-cols-4 gap-3 text-xs">
										<div>
											<p className="font-bold">{v.metrics.total_runs}</p>
											<p style={{ color: "var(--fg-muted)" }}>runs</p>
										</div>
										<div>
											<p className="font-bold">{v.metrics.failed_runs}</p>
											<p style={{ color: "var(--fg-muted)" }}>failed</p>
										</div>
										<div>
											<p
												className="font-bold"
												style={{
													color:
														v.promote_when &&
														v.metrics.error_rate >=
															v.promote_when.error_rate_below
															? "var(--error)"
															: undefined,
												}}
											>
												{(v.metrics.error_rate * 100).toFixed(1)}%
											</p>
											<p style={{ color: "var(--fg-muted)" }}>error rate</p>
										</div>
										<div>
											<p
												className="font-bold"
												style={{
													color:
														v.promote_when &&
														v.metrics.avg_drift >= v.promote_when.drift_below
															? "var(--warning)"
															: undefined,
												}}
											>
												{v.metrics.avg_drift.toFixed(3)}
											</p>
											<p style={{ color: "var(--fg-muted)" }}>avg drift</p>
										</div>
									</div>
								)}
								{v.promote_when && (
									<div
										className="mt-2 pt-2 text-xs border-t"
										style={{
											borderColor: "var(--border)",
											color: "var(--fg-muted)",
										}}
									>
										Auto-promote when: {v.promote_when.min_runs}+ runs, error
										&lt; {(v.promote_when.error_rate_below * 100).toFixed(0)}%,
										drift &lt; {v.promote_when.drift_below}, window{" "}
										{v.promote_when.window_minutes}m
									</div>
								)}
							</div>
						))}
					</div>
				</Card>
			))}
		</div>
	);
}
