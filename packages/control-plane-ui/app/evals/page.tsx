"use client";

import { RefreshCw } from "lucide-react";
import { useCallback } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { useInstances } from "@/lib/instance-context";
import type { EvalRow } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

function PassRate({ rate }: { rate: number | null }) {
	if (rate === null) return <Badge variant="muted">—</Badge>;
	const variant =
		rate >= 0.9 ? "success" : rate >= 0.7 ? "warning" : "destructive";
	return <Badge variant={variant}>{(rate * 100).toFixed(0)}%</Badge>;
}

export default function EvalsPage() {
	const { selected, selectedId } = useInstances();
	const fetcher = useCallback(
		() => api.evals(selectedId || undefined),
		[selectedId],
	);
	const { data, error, loading, refresh } = useResource<EvalRow[]>(
		`/eval?instance_id=${selectedId}`,
		fetcher,
	);
	const rows = data ?? [];
	const scopeLabel = selected ? selected.name : "the fleet";

	return (
		<>
			<PageHeader
				title="Evals"
				description={`Eval pass-rates for ${scopeLabel}, by eval set and topology.`}
				actions={
					<Button variant="outline" size="sm" onClick={refresh}>
						<RefreshCw />
						Refresh
					</Button>
				}
			/>
			<div className="p-6">
				<Card>
					<CardContent className="p-0">
						{error ? (
							<p className="p-6 text-sm text-destructive">
								Could not reach the control plane: {error}
							</p>
						) : loading ? (
							<p className="p-6 text-sm text-muted-foreground">Loading…</p>
						) : rows.length === 0 ? (
							<p className="p-6 text-sm text-muted-foreground">
								No eval results yet. Instances push them to{" "}
								<code className="rounded bg-muted px-1 py-0.5 text-xs">
									POST /aggregate/eval
								</code>
								.
							</p>
						) : (
							<Table>
								<TableHeader>
									<TableRow>
										<TableHead>Eval set</TableHead>
										<TableHead>Topology</TableHead>
										<TableHead>Pass rate</TableHead>
										<TableHead>Passed / total</TableHead>
										<TableHead>Runs</TableHead>
									</TableRow>
								</TableHeader>
								<TableBody>
									{rows.map((r) => (
										<TableRow key={`${r.eval_set}:${r.topology}`}>
											<TableCell className="font-medium">
												{r.eval_set ?? "—"}
											</TableCell>
											<TableCell>{r.topology ?? "—"}</TableCell>
											<TableCell>
												<PassRate rate={r.pass_rate} />
											</TableCell>
											<TableCell className="text-muted-foreground">
												{r.passed} / {r.total}
											</TableCell>
											<TableCell className="text-muted-foreground">
												{r.runs}
											</TableCell>
										</TableRow>
									))}
								</TableBody>
							</Table>
						)}
					</CardContent>
				</Card>
			</div>
		</>
	);
}
