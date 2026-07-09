"use client";

import { useCallback, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import type { CanaryEnvelope } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

/** Fleet canary (design 26, Layer A) — the runtime's canary router, monitored + controlled from the
 * panel. Shows per-version weights + live error rate, with promote/rollback (manage-scope). */
export function CanaryCard({ instanceId }: { instanceId: string }) {
	const fetcher = useCallback(
		() => api.instanceCanary(instanceId),
		[instanceId],
	);
	const { data, error, loading, refresh } = useResource<CanaryEnvelope>(
		`/instances/${instanceId}/canary`,
		fetcher,
		{ refreshInterval: 5000 },
	);
	const [busy, setBusy] = useState("");

	const act = async (fn: () => Promise<unknown>, key: string) => {
		setBusy(key);
		try {
			await fn();
			refresh();
		} finally {
			setBusy("");
		}
	};

	const routes = data?.canary?.routes ?? [];
	const reachable = data?.reachable ?? true;
	const unavailable =
		data?.reason === "poll-mode"
			? "Poll-mode (Mode B) instance — canary can’t be controlled remotely."
			: "Instance unavailable — couldn’t reach it for canary status.";

	return (
		<Card>
			<CardHeader>
				<CardTitle>Canary</CardTitle>
			</CardHeader>
			<CardContent className="p-0">
				{error ? (
					<p className="p-6 text-sm text-muted-foreground">
						Couldn’t reach the control plane: {error}
					</p>
				) : loading ? (
					<p className="p-6 text-sm text-muted-foreground">Loading…</p>
				) : !reachable ? (
					<p className="p-6 text-sm text-muted-foreground">{unavailable}</p>
				) : routes.length === 0 ? (
					<p className="p-6 text-sm text-muted-foreground">
						No canary routes configured on this instance.
					</p>
				) : (
					<Table>
						<TableHeader>
							<TableRow>
								<TableHead>Topology</TableHead>
								<TableHead>Version</TableHead>
								<TableHead className="text-right">Weight</TableHead>
								<TableHead className="text-right">Error rate</TableHead>
								<TableHead className="text-right">Runs</TableHead>
								<TableHead className="text-right">Actions</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{routes.flatMap((route) =>
								route.versions.map((v) => {
									// The highest-weight version is the stable/base; the rest are
									// canaries (promote to 100% or roll back).
									const maxWeight = Math.max(
										...route.versions.map((x) => x.weight),
									);
									const canary = v.weight < maxWeight;
									return (
										<TableRow key={`${route.topology}:${v.version}`}>
											<TableCell className="font-medium">
												{route.topology}
											</TableCell>
											<TableCell>
												{v.version}{" "}
												{canary ? (
													<Badge variant="warning">canary</Badge>
												) : (
													<Badge variant="muted">stable</Badge>
												)}
											</TableCell>
											<TableCell className="text-right tabular-nums">
												{v.weight}%
											</TableCell>
											<TableCell className="text-right tabular-nums">
												{v.metrics
													? `${(v.metrics.error_rate * 100).toFixed(1)}%`
													: "—"}
											</TableCell>
											<TableCell className="text-right tabular-nums">
												{v.metrics?.total_runs ?? "—"}
											</TableCell>
											<TableCell className="text-right">
												{canary ? (
													<div className="flex justify-end gap-2">
														<Button
															size="sm"
															variant="outline"
															disabled={busy !== ""}
															onClick={() =>
																act(
																	() =>
																		api.promoteCanary(
																			instanceId,
																			route.topology,
																			v.version,
																		),
																	`promote:${route.topology}:${v.version}`,
																)
															}
														>
															Promote
														</Button>
														<Button
															size="sm"
															variant="outline"
															disabled={busy !== ""}
															onClick={() =>
																act(
																	() =>
																		api.rollbackCanary(
																			instanceId,
																			route.topology,
																		),
																	`rollback:${route.topology}`,
																)
															}
														>
															Roll back
														</Button>
													</div>
												) : null}
											</TableCell>
										</TableRow>
									);
								}),
							)}
						</TableBody>
					</Table>
				)}
			</CardContent>
		</Card>
	);
}
