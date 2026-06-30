"use client";

import { Check, RefreshCw, X } from "lucide-react";
import { useCallback, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Proposal, ProposalStatus } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

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

export default function ApprovalsPage() {
	const [pendingOnly, setPendingOnly] = useState(true);
	const fetcher = useCallback(
		() => api.proposals(pendingOnly ? "pending" : undefined),
		[pendingOnly],
	);
	const { data, error, loading, refresh } = usePoll<Proposal[]>(fetcher);
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
