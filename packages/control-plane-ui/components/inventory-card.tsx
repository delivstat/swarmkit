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
	singular: string;
}[] = [
	{ key: "topologies", label: "Topologies", singular: "topology" },
	{ key: "skills", label: "Skills", singular: "skill" },
	{ key: "archetypes", label: "Archetypes", singular: "archetype" },
	{ key: "triggers", label: "Triggers", singular: "trigger" },
];

type Selection = { artifact: InstanceArtifact; kind: string };

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
	const [syncMsg, setSyncMsg] = useState<string | null>(null);
	const [selected, setSelected] = useState<Selection | null>(null);
	const [adopting, setAdopting] = useState(false);
	const [adoptMsg, setAdoptMsg] = useState<string | null>(null);
	const [adoptError, setAdoptError] = useState<string | null>(null);

	async function sync() {
		setSyncError(null);
		setSyncMsg(null);
		setBusy(true);
		try {
			const res = await api.syncInstance(instanceId);
			const d = res.delta;
			// Delta sync (design 19): show how much was transferred vs reused from cache.
			setSyncMsg(
				d
					? d.mode === "delta"
						? `Delta sync — ${d.fetched} fetched, ${d.reused} unchanged${d.removed ? `, ${d.removed} removed` : ""}.`
						: `Full sync — ${d.fetched} artifact${d.fetched === 1 ? "" : "s"} pulled.`
					: "Synced.",
			);
			refresh();
		} catch (err) {
			setSyncError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(false);
		}
	}

	async function adopt() {
		if (!selected) return;
		setAdopting(true);
		setAdoptMsg(null);
		setAdoptError(null);
		try {
			const res = await api.adoptArtifact(instanceId, {
				kind: selected.kind,
				artifact_id: selected.artifact.id,
			});
			setAdoptMsg(`Adopted ${res.kind}/${res.artifact_id} as ${res.version}.`);
		} catch (err) {
			setAdoptError(err instanceof Error ? err.message : String(err));
		} finally {
			setAdopting(false);
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
				{syncMsg && !syncError ? (
					<p className="text-xs text-muted-foreground">{syncMsg}</p>
				) : null}
				{notSynced ? (
					<p className="text-sm text-muted-foreground">
						No inventory cached yet — Sync now to pull this instance&apos;s
						topologies, skills, and archetypes.
					</p>
				) : null}
				{data
					? KINDS.map(({ key, label, singular }) => {
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
												onClick={() =>
													setSelected((s) =>
														s?.artifact === a
															? null
															: { artifact: a, kind: singular },
													)
												}
												aria-pressed={selected?.artifact === a}
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
					<div className="space-y-2">
						<div className="flex items-center justify-between gap-2">
							<div className="text-xs text-muted-foreground">
								{selected.artifact.id} · v{selected.artifact.version} ·{" "}
								{selected.artifact.content_hash.slice(0, 12)}
							</div>
							<Button
								variant="outline"
								size="sm"
								onClick={adopt}
								disabled={adopting}
								title="Promote this observed artifact into the deployable registry"
							>
								{adopting ? "Adopting…" : "Adopt into registry"}
							</Button>
						</div>
						{adoptMsg ? (
							<p className="text-xs text-success">{adoptMsg}</p>
						) : null}
						{adoptError ? (
							<p className="text-xs text-destructive">{adoptError}</p>
						) : null}
						<JsonBlock value={selected.artifact.content} />
					</div>
				) : null}
			</CardContent>
		</Card>
	);
}
