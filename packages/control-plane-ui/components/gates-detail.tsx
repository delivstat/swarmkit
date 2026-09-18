"use client";

import { useCallback, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, api } from "@/lib/api";
import type { GatesEnvelope, ReviewGate } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

/** Federated gates for one instance — §6.2 permission approvals, §6.3 input requests and a
 * funnel's multi-party role-tasks paused on the instance, resolved through the same /review API the
 * CLI + serve UI use (one queue, three front-ends). Live-pulled (Mode A / direct); a poll-mode
 * (Mode B) instance can't be federated inbound and says so. A role-task is resolved as the panel's
 * identity on the instance: if that identity is not a member of the role, the instance refuses and
 * its reason is shown on the card. */
export function GatesDetail({
	instanceId,
	instanceName,
}: {
	instanceId: string;
	instanceName: string;
}) {
	const fetcher = useCallback(
		() => api.instanceGates(instanceId),
		[instanceId],
	);
	const { data, error, loading, refresh } = useResource<GatesEnvelope>(
		`/instances/${instanceId}/review`,
		fetcher,
		{ refreshInterval: 3000 },
	);
	const [busy, setBusy] = useState<string | null>(null);
	const [refusals, setRefusals] = useState<Record<string, string>>({});

	const resolve = useCallback(
		async (itemId: string, action: string, answer = "", outcome = "") => {
			setBusy(itemId);
			setRefusals((r) => ({ ...r, [itemId]: "" }));
			try {
				await api.resolveGate(instanceId, itemId, action, answer, outcome);
				refresh();
			} catch (err) {
				// The instance's own reason (not a member of the role, item no longer pending) is the
				// thing to show; a transport failure gets the generic message.
				const msg =
					err instanceof ApiError && err.detail
						? err.detail
						: err instanceof Error
							? err.message
							: String(err);
				setRefusals((r) => ({ ...r, [itemId]: msg }));
			} finally {
				setBusy(null);
			}
		},
		[instanceId, refresh],
	);

	const gates = data?.gates ?? [];
	const reachable = data?.reachable ?? true;
	const unavailableMsg =
		data?.reason === "poll-mode"
			? "This is a poll-mode (Mode B) instance — the panel can’t reach it to resolve gates. Resolve from the instance itself."
			: "Instance unavailable — the panel couldn’t fetch its gates right now.";

	return (
		<Card>
			<CardHeader>
				<CardTitle>Gates on {instanceName}</CardTitle>
				{data?.resolves_as ? (
					<p
						className="text-xs text-muted-foreground"
						data-testid="resolves-as"
					>
						{data.resolves_as.kind === "subject" ? (
							<>
								Resolving as{" "}
								<span className="font-mono">{data.resolves_as.subject}</span>
							</>
						) : (
							<>Resolving as the enrolment key — {data.resolves_as.reason}</>
						)}
					</p>
				) : null}
			</CardHeader>
			<CardContent className="space-y-3">
				{error ? (
					<p className="text-sm text-muted-foreground">
						Couldn’t reach the control plane: {error}
					</p>
				) : loading ? (
					<p className="text-sm text-muted-foreground">Loading…</p>
				) : !reachable ? (
					<p className="text-sm text-muted-foreground">{unavailableMsg}</p>
				) : gates.length === 0 ? (
					<p className="text-sm text-muted-foreground">No pending gates.</p>
				) : (
					gates.map((gate) => (
						<GateRow
							key={gate.id}
							gate={gate}
							busy={busy === gate.id}
							refusal={refusals[gate.id] ?? ""}
							resolve={resolve}
						/>
					))
				)}
			</CardContent>
		</Card>
	);
}

function GateRow({
	gate,
	busy,
	refusal,
	resolve,
}: {
	gate: ReviewGate;
	busy: boolean;
	refusal: string;
	resolve: (
		itemId: string,
		action: string,
		answer?: string,
		outcome?: string,
	) => void;
}) {
	const [text, setText] = useState("");
	return (
		<div className="rounded-md border border-border p-3">
			<div className="flex items-center gap-2">
				<Badge variant="muted">{gate.kind}</Badge>
				<span className="text-sm font-medium">{gate.agent_id}</span>
				<span className="ml-auto font-mono text-xs text-muted-foreground">
					{gate.id.slice(0, 12)}
				</span>
			</div>

			{gate.kind === "role_task" ? (
				<>
					<p className="mt-2 text-sm">
						<span className="font-medium">{gate.role}</span> must approve{" "}
						<code className="font-mono">{gate.scope}</code>
						{gate.run_id ? (
							<span className="text-muted-foreground">
								{" "}
								· run <span className="font-mono">{gate.run_id}</span>
							</span>
						) : null}
					</p>
					<div className="mt-2 flex flex-wrap gap-2">
						<Button
							size="sm"
							disabled={busy}
							onClick={() => resolve(gate.id, "resolve", "", "approve")}
						>
							Approve
						</Button>
						<Button
							size="sm"
							variant="outline"
							disabled={busy}
							onClick={() =>
								resolve(gate.id, "resolve", "", "changes-requested")
							}
						>
							Request changes
						</Button>
						<Button
							size="sm"
							variant="destructive"
							disabled={busy}
							onClick={() => resolve(gate.id, "resolve", "", "reject")}
						>
							Reject
						</Button>
					</div>
				</>
			) : gate.kind === "permission" ? (
				<>
					<p className="mt-2 text-sm">
						Requests <code className="font-mono">{gate.capability}</code>
					</p>
					<div className="mt-2 flex gap-2">
						<Button
							size="sm"
							disabled={busy}
							onClick={() => resolve(gate.id, "approve")}
						>
							Approve
						</Button>
						<Button
							size="sm"
							variant="destructive"
							disabled={busy}
							onClick={() => resolve(gate.id, "reject")}
						>
							Reject
						</Button>
					</div>
				</>
			) : (
				<>
					<p className="mt-2 text-sm">{gate.question}</p>
					<div className="mt-2 flex flex-wrap gap-2">
						{gate.options.map((opt) => (
							<Button
								key={opt}
								size="sm"
								variant="outline"
								disabled={busy}
								onClick={() => resolve(gate.id, "answer", opt)}
							>
								{opt}
							</Button>
						))}
					</div>
					{gate.free_text_allowed ? (
						<div className="mt-2 flex gap-2">
							<input
								aria-label="Answer"
								placeholder="Or type an answer…"
								value={text}
								onChange={(e) => setText(e.target.value)}
								className="h-8 flex-1 rounded-md border border-input bg-background px-2 text-sm"
							/>
							<Button
								size="sm"
								disabled={busy || !text}
								onClick={() => resolve(gate.id, "answer", text)}
							>
								Answer
							</Button>
						</div>
					) : null}
				</>
			)}
			{refusal ? (
				<p role="alert" className="mt-2 text-sm text-destructive">
					{refusal}
				</p>
			) : null}
		</div>
	);
}
