"use client";

import { useCallback, useState } from "react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
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
import type { DriftRow, DriftStatus } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

const KINDS = ["topology", "skill", "archetype", "workspace", "trigger"];
const FIELD = "h-9 rounded-md border border-input bg-background px-2 text-sm";

const DRIFT_VARIANT: Record<DriftStatus, BadgeProps["variant"]> = {
	ok: "success",
	drift: "destructive",
	missing: "muted",
};

export function DeploymentsCard({ instanceId }: { instanceId: string }) {
	const fetcher = useCallback(() => api.drift(instanceId), [instanceId]);
	const { data, refresh } = usePoll<DriftRow[]>(fetcher, 10_000);
	const rows = data ?? [];

	const [kind, setKind] = useState("topology");
	const [artifactId, setArtifactId] = useState("");
	const [version, setVersion] = useState("");
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);

	async function setDeployment() {
		setError(null);
		setBusy(true);
		try {
			await api.setDeployment(
				instanceId,
				kind,
				artifactId.trim(),
				version.trim(),
			);
			setArtifactId("");
			setVersion("");
			refresh();
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(false);
		}
	}

	return (
		<Card>
			<CardHeader>
				<CardTitle>Deployments &amp; drift</CardTitle>
			</CardHeader>
			<CardContent className="space-y-4">
				<div className="flex flex-wrap items-end gap-2">
					<div className="flex flex-col gap-1">
						<label htmlFor="dep-kind" className="text-xs text-muted-foreground">
							Kind
						</label>
						<select
							id="dep-kind"
							value={kind}
							onChange={(e) => setKind(e.target.value)}
							className={FIELD}
						>
							{KINDS.map((k) => (
								<option key={k} value={k}>
									{k}
								</option>
							))}
						</select>
					</div>
					<div className="flex flex-col gap-1">
						<label htmlFor="dep-id" className="text-xs text-muted-foreground">
							Artifact id
						</label>
						<input
							id="dep-id"
							value={artifactId}
							onChange={(e) => setArtifactId(e.target.value)}
							placeholder="hello"
							className={FIELD}
						/>
					</div>
					<div className="flex flex-col gap-1">
						<label
							htmlFor="dep-version"
							className="text-xs text-muted-foreground"
						>
							Version
						</label>
						<input
							id="dep-version"
							value={version}
							onChange={(e) => setVersion(e.target.value)}
							placeholder="v2"
							className={`${FIELD} font-mono`}
						/>
					</div>
					<Button
						onClick={setDeployment}
						disabled={busy || !artifactId.trim() || !version.trim()}
					>
						{busy ? "Setting…" : "Set intended version"}
					</Button>
				</div>
				{error ? <p className="text-sm text-destructive">{error}</p> : null}

				{rows.length === 0 ? (
					<p className="text-sm text-muted-foreground">
						No intended deployments set for this instance.
					</p>
				) : (
					<Table>
						<TableHeader>
							<TableRow>
								<TableHead>Kind</TableHead>
								<TableHead>Artifact</TableHead>
								<TableHead>Intended</TableHead>
								<TableHead>Actual</TableHead>
								<TableHead>Status</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{rows.map((r) => (
								<TableRow key={`${r.kind}:${r.id}`}>
									<TableCell>
										<Badge variant="secondary">{r.kind}</Badge>
									</TableCell>
									<TableCell className="font-medium">{r.id}</TableCell>
									<TableCell className="font-mono text-xs">
										{r.intended_version}
									</TableCell>
									<TableCell className="font-mono text-xs text-muted-foreground">
										{r.actual_version ?? "—"}
									</TableCell>
									<TableCell>
										<Badge variant={DRIFT_VARIANT[r.status]}>{r.status}</Badge>
									</TableCell>
								</TableRow>
							))}
						</TableBody>
					</Table>
				)}
			</CardContent>
		</Card>
	);
}
