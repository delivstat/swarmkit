"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";
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
import type { ArtifactSummary } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

export default function ArtifactsPage() {
	const fetcher = useCallback(() => api.artifacts(), []);
	const { data, error, loading, refresh } = usePoll<ArtifactSummary[]>(fetcher);
	const rows = data ?? [];

	return (
		<>
			<PageHeader
				title="Artifacts"
				description="Versioned topologies, skills, archetypes, workspaces, and triggers in the registry."
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
								No artifacts registered yet. Register one with{" "}
								<code className="rounded bg-muted px-1 py-0.5 text-xs">
									POST /artifacts/&#123;kind&#125;/&#123;id&#125;/versions
								</code>
								.
							</p>
						) : (
							<Table>
								<TableHeader>
									<TableRow>
										<TableHead>Kind</TableHead>
										<TableHead>ID</TableHead>
										<TableHead>Latest</TableHead>
										<TableHead>Versions</TableHead>
										<TableHead>Hash</TableHead>
									</TableRow>
								</TableHeader>
								<TableBody>
									{rows.map((r) => (
										<TableRow key={`${r.kind}:${r.id}`}>
											<TableCell>
												<Badge variant="secondary">{r.kind}</Badge>
											</TableCell>
											<TableCell className="font-medium">
												<Link
													href={`/artifacts/${r.kind}/${encodeURIComponent(r.id)}`}
													className="hover:underline"
												>
													{r.id}
												</Link>
											</TableCell>
											<TableCell className="font-mono text-xs">
												{r.latest_version}
											</TableCell>
											<TableCell className="text-muted-foreground">
												{r.versions}
											</TableCell>
											<TableCell className="font-mono text-xs text-muted-foreground">
												{r.latest_hash.slice(0, 12)}
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
