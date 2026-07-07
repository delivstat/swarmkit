"use client";

import { Check, RefreshCw, Sparkles, X } from "lucide-react";
import { useCallback, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, api } from "@/lib/api";
import { useInstances } from "@/lib/instance-context";
import type { Gap, Proposal, ProposalStatus } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

const STATUS_VARIANT: Record<ProposalStatus, BadgeProps["variant"]> = {
	pending: "warning",
	approved: "success",
	rejected: "destructive",
};

function ProposalCard({ p, onChange }: { p: Proposal; onChange: () => void }) {
	const [busy, setBusy] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);

	async function act(fn: () => Promise<unknown>, label: string) {
		setBusy(label);
		setError(null);
		try {
			await fn();
			onChange();
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
			setBusy(null);
		}
	}

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex flex-wrap items-center gap-2 text-base">
					<Badge variant="secondary">{p.kind}</Badge>
					<span className="font-mono">{p.artifact_id}</span>
					<Badge variant={STATUS_VARIANT[p.status]}>{p.status}</Badge>
					{p.signal ? (
						<span className="text-xs font-normal text-muted-foreground">
							signal: {p.signal}
						</span>
					) : null}
				</CardTitle>
			</CardHeader>
			<CardContent className="space-y-3">
				<div className="text-xs text-muted-foreground">
					proposed by <span className="font-mono">{p.proposed_by || "—"}</span>{" "}
					· {p.created_at}
					{p.status === "approved" ? (
						<>
							{" "}
							· approved by{" "}
							<span className="font-mono">{p.approved_by || "—"}</span> →
							published <span className="font-mono">{p.published_version}</span>
						</>
					) : null}
					{p.status === "rejected" ? (
						<> · rejected{p.reason ? `: ${p.reason}` : ""}</>
					) : null}
				</div>

				<pre className="max-h-48 overflow-auto rounded-md bg-muted p-3 font-mono text-xs">
					{typeof p.content === "string"
						? p.content
						: JSON.stringify(p.content, null, 2)}
				</pre>

				{error ? <p className="text-sm text-destructive">{error}</p> : null}

				{p.status === "pending" ? (
					<div className="flex gap-2">
						<Button
							size="sm"
							disabled={busy !== null}
							onClick={() => act(() => api.approveProposal(p.id), "approve")}
						>
							<Check />
							{busy === "approve" ? "Approving…" : "Approve & publish"}
						</Button>
						<Button
							size="sm"
							variant="outline"
							disabled={busy !== null}
							onClick={() => {
								const reason = window.prompt("Reason for rejecting?") ?? "";
								void act(() => api.rejectProposal(p.id, reason), "reject");
							}}
						>
							<X />
							Reject
						</Button>
					</div>
				) : null}
			</CardContent>
		</Card>
	);
}

function GapsPanel({ onProposed }: { onProposed: () => void }) {
	const { instances, selected } = useInstances();
	const fetcher = useCallback(() => api.gaps(), []);
	const { data, refresh } = useResource<Gap[]>("/gaps", fetcher, {
		refreshInterval: 30_000,
	});
	const gaps = data ?? [];
	const [busy, setBusy] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);

	// Auto-draft drives the authoring swarm on a Mode A instance.
	const target =
		selected?.connection === "direct"
			? selected
			: instances.find((i) => i.connection === "direct");

	if (gaps.length === 0) return null;

	async function draft(gap: Gap) {
		if (!target) {
			setError("No Mode A instance available to draft a fix.");
			return;
		}
		setBusy(gap.capability);
		setError(null);
		try {
			await api.proposeFromGap({
				instance_id: target.id,
				capability: gap.capability,
				description: gap.description ?? "",
			});
			refresh();
			onProposed();
		} catch (e) {
			setError(e instanceof ApiError ? e.message : String(e));
		} finally {
			setBusy(null);
		}
	}

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2 text-base">
					<Sparkles className="size-4" />
					Skill gaps
					<span className="text-xs font-normal text-muted-foreground">
						ranked across the fleet · draft targets{" "}
						<span className="font-mono">{target?.name ?? "—"}</span>
					</span>
				</CardTitle>
			</CardHeader>
			<CardContent className="space-y-2">
				{error ? <p className="text-sm text-destructive">{error}</p> : null}
				{gaps.map((g) => (
					<div
						key={g.capability}
						className="flex items-center justify-between gap-3 border-b py-2 last:border-0"
					>
						<div className="min-w-0">
							<span className="font-mono text-sm">{g.capability}</span>
							<span className="ml-2 text-xs text-muted-foreground">
								{g.occurrences}× across {g.instances} instance
								{g.instances === 1 ? "" : "s"}
							</span>
						</div>
						<Button
							size="sm"
							variant="outline"
							disabled={busy !== null || !target}
							onClick={() => draft(g)}
						>
							<Sparkles />
							{busy === g.capability ? "Drafting…" : "Draft a fix"}
						</Button>
					</div>
				))}
			</CardContent>
		</Card>
	);
}

export default function ApprovalsPage() {
	const [pendingOnly, setPendingOnly] = useState(true);
	const fetcher = useCallback(
		() => api.proposals(pendingOnly ? "pending" : undefined),
		[pendingOnly],
	);
	// Key varies with the filter so switching pending/all is a distinct cache entry.
	const { data, error, loading, refresh } = useResource<Proposal[]>(
		pendingOnly ? "/proposals?status=pending" : "/proposals",
		fetcher,
	);
	const rows = data ?? [];

	return (
		<>
			<PageHeader
				title="Approvals"
				description="Human-gated growth-loop proposals. Approving publishes the change as a registry version."
				actions={
					<>
						<Button
							variant={pendingOnly ? "default" : "outline"}
							size="sm"
							onClick={() => setPendingOnly((v) => !v)}
						>
							{pendingOnly ? "Pending only" : "All"}
						</Button>
						<Button variant="outline" size="sm" onClick={refresh}>
							<RefreshCw />
							Refresh
						</Button>
					</>
				}
			/>
			<div className="space-y-4 p-6">
				<GapsPanel onProposed={refresh} />
				{error ? (
					<p className="text-sm text-destructive">
						Could not reach the control plane: {error}
					</p>
				) : loading ? (
					<p className="text-sm text-muted-foreground">Loading…</p>
				) : rows.length === 0 ? (
					<p className="text-sm text-muted-foreground">
						{pendingOnly ? "No pending proposals." : "No proposals yet."}
					</p>
				) : (
					rows.map((p) => <ProposalCard key={p.id} p={p} onChange={refresh} />)
				)}
			</div>
		</>
	);
}
