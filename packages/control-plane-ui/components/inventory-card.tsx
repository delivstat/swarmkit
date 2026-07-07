"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { JsonBlock } from "@/components/ui/json-block";
import { api } from "@/lib/api";
import type { CachedState, InstanceArtifact } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

const KINDS: {
	key: keyof CachedState["state"]["artifacts"];
	label: string;
}[] = [
	{ key: "topologies", label: "Topologies" },
	{ key: "skills", label: "Skills" },
	{ key: "archetypes", label: "Archetypes" },
	{ key: "triggers", label: "Triggers" },
];

/**
 * The instance's cached inventory (design 19 Phase 1): the topologies/skills/archetypes the panel
 * last pulled via /fleet/state — with content, and served from cache so it renders even when the
 * instance is offline. "Sync now" re-pulls. Click an artifact to view its content.
 */
export function InventoryCard({ instanceId }: { instanceId: string }) {
	const fetcher = useCallback(
		() => api.instanceState(instanceId),
		[instanceId],
	);
	// Pulled on demand (Sync now), not polled — the cache only changes on an explicit sync.
	const { data, loading, refresh } = useResource<CachedState>(
		`/instances/${instanceId}/state`,
		fetcher,
		{ refreshInterval: 0 },
	);
	const [busy, setBusy] = useState(false);
	const [syncError, setSyncError] = useState<string | null>(null);
	const [selected, setSelected] = useState<InstanceArtifact | null>(null);

	async function sync() {
		setSyncError(null);
		setBusy(true);
		try {
			await api.syncInstance(instanceId);
			refresh();
		} catch (err) {
			setSyncError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(false);
		}
	}

	const notSynced = !data && !loading; // GET /state 404s until the first sync

	return (
		<Card>
			<CardHeader className="flex-row items-start justify-between space-y-0">
				<div className="space-y-1">
					<CardTitle>Inventory</CardTitle>
					{data ? (
						<p className="text-xs text-muted-foreground">
							{data.state.workspace_id} · synced{" "}
							{new Date(data.synced_at).toLocaleString()}
						</p>
					) : null}
				</div>
				<Button variant="outline" size="sm" onClick={sync} disabled={busy}>
					<RefreshCw className={busy ? "animate-spin" : ""} />
					{busy ? "Syncing…" : "Sync now"}
				</Button>
			</CardHeader>
			<CardContent className="space-y-4">
				{syncError ? (
					<p className="text-sm text-destructive">{syncError}</p>
				) : null}
				{notSynced ? (
					<p className="text-sm text-muted-foreground">
						No inventory cached yet — Sync now to pull this instance&apos;s
						topologies, skills, and archetypes.
					</p>
				) : null}
				{data
					? KINDS.map(({ key, label }) => {
							const items = data.state.artifacts[key] ?? [];
							if (items.length === 0) return null;
							return (
								<div key={key}>
									<div className="mb-1 flex items-center gap-2 text-sm font-medium">
										{label}
										<Badge variant="muted">{items.length}</Badge>
									</div>
									<div className="flex flex-wrap gap-1">
										{items.map((a) => (
											<button
												key={a.id}
												type="button"
												onClick={() => setSelected((s) => (s === a ? null : a))}
												aria-pressed={selected === a}
												className="rounded border px-2 py-0.5 font-mono text-xs outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring aria-pressed:bg-accent"
											>
												{a.id}
											</button>
										))}
									</div>
								</div>
							);
						})
					: null}
				{selected ? (
					<div>
						<div className="mb-1 text-xs text-muted-foreground">
							{selected.id} · v{selected.version} ·{" "}
							{selected.content_hash.slice(0, 12)}
						</div>
						<JsonBlock value={selected.content} />
					</div>
				) : null}
			</CardContent>
		</Card>
	);
}
