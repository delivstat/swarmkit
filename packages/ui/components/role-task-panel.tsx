"use client";

// The role-task panel (design/details/pipeline-gate-approval-ui.md): the approval surface for a
// stage parked on a multi-party gate. It lives inside the run-node inspector on purpose — the
// approver sees the stage, its input and the artifact it produced *before* deciding. An inbox with
// no context is how gates get rubber-stamped, which is the behaviour `swarmkit comprehension`
// exists to detect after the fact.
//
// The resolver identity is the authenticated session, never a field this component sends. It shows
// who that is, because under a role registry only some of a gate's tasks are yours to act on and
// the server is the only thing that knows which.

import { Check, Loader2, RotateCcw, ShieldCheck, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type {
	DecisionOutcome,
	GateStatusDetail,
	RoleTaskItem,
	Whoami,
} from "@/lib/types";

export interface RoleTaskPanelProps {
	correlationId: string;
	/** The parked stage — its id is also the gate name (`<correlation_id>:<stage>`). */
	stageId: string;
}

const STATUS_STYLE: Record<RoleTaskItem["status"], string> = {
	approved: "border-emerald-500 text-emerald-500",
	rejected: "border-red-500 text-red-500",
	"changes-requested": "border-sky-500 text-sky-500",
	pending: "border-amber-500 text-amber-500",
};

export function RoleTaskPanel({ correlationId, stageId }: RoleTaskPanelProps) {
	const [gate, setGate] = useState<GateStatusDetail | null>(null);
	const [me, setMe] = useState<Whoami | null>(null);
	const [busy, setBusy] = useState<string | null>(null);
	// Per-row, so a comment typed against one role-task cannot land on another.
	const [comments, setComments] = useState<Record<string, string>>({});
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);

	const load = useCallback(() => {
		return api
			.gateStatus(correlationId, stageId)
			.then(setGate)
			.catch(() => setGate(null))
			.finally(() => setLoading(false));
	}, [correlationId, stageId]);

	useEffect(() => {
		setLoading(true);
		load();
	}, [load]);

	useEffect(() => {
		api
			.whoami()
			.then(setMe)
			.catch(() => setMe(null));
	}, []);

	async function resolve(id: string, outcome: DecisionOutcome) {
		setBusy(id);
		setError(null);
		try {
			await api.reviewResolve(id, outcome, (comments[id] ?? "").trim());
			setComments((c) => ({ ...c, [id]: "" }));
			await load();
		} catch (e) {
			// The server's refusal names the reason — "alice is not a member of role X" — which is
			// the only place that judgement exists. Surface it verbatim rather than a generic error.
			setError(String(e).replace(/^Error:\s*/, ""));
		} finally {
			setBusy(null);
		}
	}

	if (loading) {
		return (
			<span className="flex items-center gap-1.5 text-xs text-muted-foreground">
				<Loader2 className="h-3.5 w-3.5 animate-spin" /> loading approvals…
			</span>
		);
	}

	if (!gate || gate.items.length === 0) {
		return (
			<p className="text-xs text-muted-foreground">
				This stage is parked, but no multi-party approval gate was opened for
				it. Release it with <code>swarmkit pipeline advance</code>.
			</p>
		);
	}

	return (
		<div className="flex flex-col gap-3">
			<div className="flex items-center gap-2">
				<Badge variant="outline" className="uppercase">
					{gate.status}
				</Badge>
				{!gate.quorum_evaluated ? (
					<span
						className="text-[11px] text-muted-foreground"
						title="The gate's approval policy was not reachable, so this status folds the tasks (every task must approve) instead of evaluating quorum."
					>
						quorum not evaluated
					</span>
				) : null}
			</div>

			<ul className="flex flex-col gap-2">
				{gate.items.map((item) => (
					<li
						key={item.id}
						className="flex flex-col gap-1.5 rounded-md border p-2"
					>
						<div className="flex items-center gap-2">
							<span className="font-medium">{item.role}</span>
							<Badge
								variant="outline"
								className={`text-[10px] ${STATUS_STYLE[item.status]}`}
							>
								{item.status}
							</Badge>
							<code className="ml-auto text-[10px] text-muted-foreground">
								{item.scope}
							</code>
						</div>

						{item.status === "pending" ? (
							<>
								<textarea
									value={comments[item.id] ?? ""}
									onChange={(e) =>
										setComments((c) => ({ ...c, [item.id]: e.target.value }))
									}
									placeholder="Why? Relayed to the agent and recorded on the audit."
									rows={2}
									className="w-full resize-y rounded-md border bg-background p-2 text-xs"
								/>
								<div className="flex flex-wrap gap-2">
									<Button
										type="button"
										size="sm"
										disabled={busy === item.id}
										onClick={() => resolve(item.id, "approve")}
									>
										<Check className="mr-1 h-3.5 w-3.5" />
										Approve
									</Button>
									<Button
										type="button"
										size="sm"
										variant="outline"
										disabled={busy === item.id}
										className="border-sky-500 text-sky-500 hover:bg-sky-500/10"
										onClick={() => resolve(item.id, "changes-requested")}
									>
										<RotateCcw className="mr-1 h-3.5 w-3.5" />
										Request changes
									</Button>
									<Button
										type="button"
										size="sm"
										variant="outline"
										disabled={busy === item.id}
										className="border-destructive text-destructive hover:bg-destructive/10"
										onClick={() => resolve(item.id, "reject")}
									>
										<X className="mr-1 h-3.5 w-3.5" />
										Reject
									</Button>
								</div>
								{/* The distinction is the whole point of the third button, and it is not
								    guessable from the labels. */}
								<p className="text-[11px] text-muted-foreground">
									<strong>Request changes</strong> re-runs the stage with your
									comment. <strong>Reject</strong> ends the run.
								</p>
							</>
						) : (
							<div className="flex flex-col gap-1">
								<span className="text-xs text-muted-foreground">
									{item.status} by {item.resolved_by || "—"}
									{item.round ? ` · round ${item.round}` : null}
								</span>
								{item.comment ? (
									<p className="rounded-md border-l-2 border-muted-foreground/40 bg-muted/40 px-2 py-1 text-xs italic">
										“{item.comment}”
									</p>
								) : null}
							</div>
						)}
					</li>
				))}
			</ul>

			{error ? <p className="text-xs text-red-500">{error}</p> : null}

			{me ? (
				<p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
					<ShieldCheck className="h-3.5 w-3.5" />
					Acting as <span className="font-mono">{me.client_id}</span>
					{me.mode === "none"
						? " — serve has no auth configured, so every caller is anonymous"
						: ""}
				</p>
			) : null}
		</div>
	);
}
