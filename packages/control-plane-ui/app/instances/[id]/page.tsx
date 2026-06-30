"use client";

import { ArrowLeft, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { CommandQueue } from "@/components/command-queue";
import { DeploymentsCard } from "@/components/deployments-card";
import { ConnectionBadge, HealthBadge } from "@/components/health-badge";
import { LiveJobsCard } from "@/components/live-jobs-card";
import { MintPanel } from "@/components/mint-panel";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Instance } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

function Field({
	label,
	children,
}: { label: string; children: React.ReactNode }) {
	return (
		<div className="space-y-1">
			<div className="text-xs text-muted-foreground">{label}</div>
			<div className="text-sm">{children}</div>
		</div>
	);
}

export default function InstanceDetailPage() {
	const params = useParams();
	const router = useRouter();
	const id = String(params.id);

	const fetcher = useCallback(() => api.getInstance(id), [id]);
	const {
		data: instance,
		error,
		loading,
		refresh,
	} = usePoll<Instance>(fetcher);

	const [busy, setBusy] = useState<string | null>(null);
	const [actionError, setActionError] = useState<string | null>(null);

	async function verify() {
		setBusy("verify");
		setActionError(null);
		try {
			await api.verifyInstance(id);
			refresh();
		} catch (err) {
			setActionError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(null);
		}
	}

	async function remove() {
		if (
			!confirm(
				`Delete instance "${instance?.name ?? id}"? This cannot be undone.`,
			)
		)
			return;
		setBusy("delete");
		try {
			await api.deleteInstance(id);
			router.push("/instances");
		} catch (err) {
			setActionError(err instanceof Error ? err.message : String(err));
			setBusy(null);
		}
	}

	if (loading && !instance) {
		return (
			<>
				<PageHeader title="Instance" />
				<p className="p-6 text-sm text-muted-foreground">Loading…</p>
			</>
		);
	}

	if (error && !instance) {
		return (
			<>
				<PageHeader title="Instance" />
				<p className="p-6 text-sm text-destructive">
					Could not load instance: {error}
				</p>
			</>
		);
	}

	if (!instance) return null;

	return (
		<>
			<PageHeader
				title={instance.name}
				description={instance.endpoint}
				actions={
					<>
						<Button variant="ghost" size="sm" onClick={refresh}>
							<RefreshCw />
							Refresh
						</Button>
						{instance.connection === "direct" ? (
							<Button
								variant="outline"
								size="sm"
								onClick={verify}
								disabled={busy === "verify"}
							>
								<ShieldCheck />
								{busy === "verify" ? "Verifying…" : "Verify"}
							</Button>
						) : null}
						<Button
							variant="destructive"
							size="sm"
							onClick={remove}
							disabled={busy === "delete"}
						>
							<Trash2 />
							Delete
						</Button>
					</>
				}
			/>

			<div className="space-y-6 p-6">
				<Link
					href="/instances"
					className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
				>
					<ArrowLeft className="size-4" />
					All instances
				</Link>

				{actionError ? (
					<p className="text-sm text-destructive">{actionError}</p>
				) : null}

				<Card>
					<CardHeader>
						<CardTitle>Overview</CardTitle>
					</CardHeader>
					<CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
						<Field label="Health">
							<HealthBadge health={instance.health} />
						</Field>
						<Field label="Connection">
							<ConnectionBadge connection={instance.connection} />
						</Field>
						<Field label="Granted tier">{instance.tier}</Field>
						<Field label="Endpoint">
							<span className="font-mono text-xs">{instance.endpoint}</span>
						</Field>
						<Field label="Schema version">
							{instance.schema_version || "—"}
						</Field>
						<Field label="Last seen">{instance.last_seen ?? "—"}</Field>
						<Field label="Instance id">
							<span className="font-mono text-xs">{instance.id}</span>
						</Field>
						<Field label="Created">{instance.created_at}</Field>
					</CardContent>
				</Card>

				<MintPanel instance={instance} onMinted={refresh} />

				{instance.connection === "poll" ? (
					<CommandQueue instanceId={instance.id} tier={instance.tier} />
				) : (
					<LiveJobsCard instanceId={instance.id} />
				)}

				<DeploymentsCard instanceId={instance.id} />

				<Card>
					<CardHeader>
						<CardTitle>Capabilities</CardTitle>
					</CardHeader>
					<CardContent>
						{Object.keys(instance.capabilities).length === 0 ? (
							<p className="text-sm text-muted-foreground">
								None reported yet.
							</p>
						) : (
							<pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
								{JSON.stringify(instance.capabilities, null, 2)}
							</pre>
						)}
					</CardContent>
				</Card>
			</div>
		</>
	);
}
