"use client";

import { useCallback, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { CanaryStatus } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { cn } from "@/lib/utils";

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
			<h2 className="mb-4 text-xl font-bold">Canary Deployments</h2>
			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}
			{actionMsg && (
				<p className="mb-3 rounded-md bg-muted px-3 py-2 text-sm">
					{actionMsg}
				</p>
			)}
			{data && !data.enabled && (
				<Card>
					<p className="text-sm text-muted-foreground">
						No canary routes configured. Add <code>server.canary.routes</code>{" "}
						to workspace.yaml.
					</p>
				</Card>
			)}
			{data?.routes.map((route) => (
				<Card key={route.topology} className="mb-4">
					<div className="mb-4 flex items-center justify-between">
						<CardTitle>{route.topology}</CardTitle>
						<Button
							type="button"
							variant="outline"
							size="sm"
							className="border-destructive text-destructive hover:bg-destructive/10"
							onClick={() => rollback(route.topology)}
						>
							Rollback
						</Button>
					</div>
					<div className="space-y-3">
						{route.versions.map((v) => (
							<div
								key={v.version}
								className="rounded-md border bg-background p-3"
							>
								<div className="mb-2 flex items-center justify-between">
									<span className="font-medium">
										v{v.version}{" "}
										<span
											className={cn(
												"ml-1 text-sm font-bold",
												v.weight > 0 ? "text-sky-500" : "text-muted-foreground",
											)}
										>
											{v.weight}%
										</span>
									</span>
									{v.weight < 100 && (
										<Button
											type="button"
											size="sm"
											onClick={() => promote(route.topology, v.version)}
										>
											Promote
										</Button>
									)}
								</div>
								{v.metrics && (
									<div className="grid grid-cols-4 gap-3 text-xs">
										<div>
											<p className="font-bold">{v.metrics.total_runs}</p>
											<p className="text-muted-foreground">runs</p>
										</div>
										<div>
											<p className="font-bold">{v.metrics.failed_runs}</p>
											<p className="text-muted-foreground">failed</p>
										</div>
										<div>
											<p
												className={cn(
													"font-bold",
													v.promote_when &&
														v.metrics.error_rate >=
															v.promote_when.error_rate_below &&
														"text-destructive",
												)}
											>
												{(v.metrics.error_rate * 100).toFixed(1)}%
											</p>
											<p className="text-muted-foreground">error rate</p>
										</div>
										<div>
											<p
												className={cn(
													"font-bold",
													v.promote_when &&
														v.metrics.avg_drift >= v.promote_when.drift_below &&
														"text-warning",
												)}
											>
												{v.metrics.avg_drift.toFixed(3)}
											</p>
											<p className="text-muted-foreground">avg drift</p>
										</div>
									</div>
								)}
								{v.promote_when && (
									<div className="mt-2 border-t pt-2 text-xs text-muted-foreground">
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
