"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";

import { ConnectionBadge, HealthBadge } from "@/components/health-badge";
import { PageHeader } from "@/components/page-header";
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
import type { Instance } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

export default function InstancesPage() {
	const { data, error, loading, refresh } = usePoll<Instance[]>(
		api.listInstances,
	);
	const instances = data ?? [];

	return (
		<>
			<PageHeader
				title="Instances"
				description="Every swarmkit serve deployment registered with this control plane."
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
						) : instances.length === 0 ? (
							<p className="p-6 text-sm text-muted-foreground">
								No instances enrolled yet.
							</p>
						) : (
							<Table>
								<TableHeader>
									<TableRow>
										<TableHead>Name</TableHead>
										<TableHead>Endpoint</TableHead>
										<TableHead>Connection</TableHead>
										<TableHead>Health</TableHead>
										<TableHead>Schema</TableHead>
										<TableHead>Last seen</TableHead>
									</TableRow>
								</TableHeader>
								<TableBody>
									{instances.map((inst) => (
										<TableRow key={inst.id}>
											<TableCell className="font-medium">
												<Link
													href={`/instances/${inst.id}`}
													className="hover:underline"
												>
													{inst.name}
												</Link>
											</TableCell>
											<TableCell className="font-mono text-xs text-muted-foreground">
												{inst.endpoint}
											</TableCell>
											<TableCell>
												<ConnectionBadge connection={inst.connection} />
											</TableCell>
											<TableCell>
												<HealthBadge health={inst.health} />
											</TableCell>
											<TableCell className="text-muted-foreground">
												{inst.schema_version || "—"}
											</TableCell>
											<TableCell className="text-muted-foreground">
												{inst.last_seen ?? "—"}
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
