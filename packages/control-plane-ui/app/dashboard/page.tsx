"use client";

import { Activity, Radio, Server, ServerCog } from "lucide-react";
import Link from "next/link";

import { ConnectionBadge, HealthBadge } from "@/components/health-badge";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Instance } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

function StatCard({
	label,
	value,
	icon: Icon,
}: {
	label: string;
	value: number | string;
	icon: typeof Server;
}) {
	return (
		<Card>
			<CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
				<CardTitle className="text-sm font-medium text-muted-foreground">
					{label}
				</CardTitle>
				<Icon className="size-4 text-muted-foreground" />
			</CardHeader>
			<CardContent>
				<div className="text-2xl font-semibold">{value}</div>
			</CardContent>
		</Card>
	);
}

export default function DashboardPage() {
	const { data, error, loading } = usePoll<Instance[]>(api.listInstances);
	const instances = data ?? [];

	const total = instances.length;
	const healthy = instances.filter((i) => i.health === "healthy").length;
	const direct = instances.filter((i) => i.connection === "direct").length;
	const poll = instances.filter((i) => i.connection === "poll").length;

	return (
		<>
			<PageHeader
				title="Fleet"
				description="Overview of all registered SwarmKit instances."
			/>
			<div className="space-y-6 p-6">
				{error ? (
					<Card>
						<CardContent className="py-6 text-sm text-destructive">
							Could not reach the control plane: {error}
						</CardContent>
					</Card>
				) : null}

				<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
					<StatCard
						label="Instances"
						value={loading ? "—" : total}
						icon={Server}
					/>
					<StatCard
						label="Healthy"
						value={loading ? "—" : healthy}
						icon={Activity}
					/>
					<StatCard
						label="Direct (Mode A)"
						value={loading ? "—" : direct}
						icon={ServerCog}
					/>
					<StatCard
						label="Poll (Mode B)"
						value={loading ? "—" : poll}
						icon={Radio}
					/>
				</div>

				<Card>
					<CardHeader>
						<CardTitle>Instances</CardTitle>
					</CardHeader>
					<CardContent className="space-y-2">
						{loading ? (
							<p className="text-sm text-muted-foreground">Loading…</p>
						) : total === 0 ? (
							<p className="text-sm text-muted-foreground">
								No instances enrolled yet.{" "}
								<Link
									href="/instances/new"
									className="text-foreground underline"
								>
									Enroll one
								</Link>
								.
							</p>
						) : (
							instances.map((inst) => (
								<Link
									key={inst.id}
									href={`/instances/${inst.id}`}
									className="flex items-center justify-between rounded-md border p-3 transition-colors hover:bg-accent"
								>
									<div className="space-y-0.5">
										<div className="font-medium">{inst.name}</div>
										<div className="text-xs text-muted-foreground">
											{inst.endpoint}
										</div>
									</div>
									<div className="flex items-center gap-2">
										<ConnectionBadge connection={inst.connection} />
										<HealthBadge health={inst.health} />
									</div>
								</Link>
							))
						)}
					</CardContent>
				</Card>
			</div>
		</>
	);
}
