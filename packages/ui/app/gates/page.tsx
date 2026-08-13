"use client";

import { useCallback, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { ReviewGate } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

/** The run to open for a gate.
 *
 * Prefer the server's `run_id`, which is always a job id. The fallback splits `gate_id` on the last
 * colon, for items written before the field existed.
 *
 * This page used to ALWAYS split, and link at `/runs` — the pipeline saga view. That is one of the
 * two gate-id shapes: a stage's is `<correlation>:<stage>`, but an in-node funnel gate's is
 * `<run>:<agent>`, so `runOf` returned a topology id and the link searched for a pipeline run that
 * does not exist. Every gated topology run pointed at "No pipeline runs to show". */
export function runOf(gate: { gate_id: string; run_id?: string }): string {
	if (gate.run_id) return gate.run_id;
	const i = gate.gate_id.lastIndexOf(":");
	return i === -1 ? gate.gate_id : gate.gate_id.slice(0, i);
}

/** Pending human decisions — §6.2 permission approvals, §6.3 input requests, and multi-party
 * approval role-tasks. Same review queue + HTTP API the CLI (`swarmkit review`) and fleet UI use,
 * so a gate resolves identically whichever surface an operator picks.
 *
 * Role-tasks are LISTED here but resolved in the run view: this page has no artifact to show, and
 * approving without seeing what you are approving is the failure mode the whole gate exists to
 * prevent (design/details/pipeline-gate-approval-ui.md). The inbox tells you a decision is waiting;
 * the run view is where you make it. */
export default function GatesPage() {
	const fetchGates = useCallback(() => api.reviewPending(), []);
	const { data, error, loading, refetch } = usePoll<ReviewGate[]>(
		fetchGates,
		3000,
	);
	const [busy, setBusy] = useState<string | null>(null);

	async function act(fn: () => Promise<unknown>, id: string) {
		setBusy(id);
		try {
			await fn();
			await refetch?.();
		} finally {
			setBusy(null);
		}
	}

	return (
		<div>
			<h2 className="mb-4 text-xl font-bold">Gates</h2>
			<p className="mb-4 text-sm text-muted-foreground">
				Runs paused for a human decision — permission approvals, input
				questions, and multi-party approval role-tasks. Resolving here is the
				same action as <code>swarmkit review</code>.
			</p>

			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}
			{data && data.length === 0 && (
				<p className="text-sm text-muted-foreground">No pending gates.</p>
			)}

			<div className="grid gap-3">
				{data?.map((gate) => (
					<GateCard
						key={gate.id}
						gate={gate}
						busy={busy === gate.id}
						act={act}
					/>
				))}
			</div>
		</div>
	);
}

function GateCard({
	gate,
	busy,
	act,
}: {
	gate: ReviewGate;
	busy: boolean;
	act: (fn: () => Promise<unknown>, id: string) => Promise<void>;
}) {
	const [text, setText] = useState("");
	const [comment, setComment] = useState("");
	return (
		<Card>
			<div className="flex items-center gap-2">
				<Badge variant="outline" className="uppercase">
					{gate.kind}
				</Badge>
				<CardTitle>{gate.agent_id}</CardTitle>
				<span className="ml-auto font-mono text-xs text-muted-foreground">
					{gate.id.slice(0, 12)}
				</span>
			</div>

			{gate.kind === "permission" && (
				<>
					<p className="mt-2 text-sm">
						Requests permission for{" "}
						<code className="font-mono">{gate.capability}</code>
					</p>
					<Input
						value={comment}
						onChange={(e) => setComment(e.target.value)}
						placeholder="Why? Relayed to the agent when it resumes."
						className="mt-3"
					/>
					<div className="mt-2 flex gap-2">
						<Button
							type="button"
							size="sm"
							disabled={busy}
							onClick={() =>
								act(() => api.reviewApprove(gate.id, comment.trim()), gate.id)
							}
						>
							Approve
						</Button>
						<Button
							type="button"
							variant="outline"
							size="sm"
							disabled={busy}
							className="border-destructive text-destructive hover:bg-destructive/10"
							onClick={() =>
								act(() => api.reviewReject(gate.id, comment.trim()), gate.id)
							}
						>
							Reject
						</Button>
					</div>
					{/* A permission decision is relayed as the harness's resume statement, so a
					    condition ("staging only") actually reaches the agent rather than being
					    flattened to a boolean. */}
					<p className="mt-1 text-[11px] text-muted-foreground">
						A comment is relayed to the agent when it resumes.
					</p>
				</>
			)}

			{gate.kind === "role_task" && (
				<>
					<p className="mt-2 text-sm">
						Approval from role <span className="font-medium">{gate.role}</span>{" "}
						for <code className="font-mono text-xs">{gate.scope}</code>
					</p>
					{gate.comment ? (
						<p className="mt-2 rounded-md border-l-2 border-muted-foreground/40 bg-muted/40 px-2 py-1 text-xs italic">
							“{gate.comment}”
						</p>
					) : null}
					<div className="mt-3 flex items-center gap-3">
						<a
							className="text-sm underline underline-offset-4"
							href={`/job/?id=${encodeURIComponent(runOf(gate))}`}
						>
							Open the run that produced this →
						</a>
						<span className="text-xs text-muted-foreground">
							every gated run has a job row — a stage's and a one-shot run's alike
						</span>
					</div>
				</>
			)}

			{gate.kind === "input" && (
				<>
					<p className="mt-2 text-sm">{gate.question}</p>
					<div className="mt-2 flex flex-wrap gap-2">
						{gate.options.map((opt) => (
							<Button
								type="button"
								key={opt}
								variant="outline"
								size="sm"
								disabled={busy}
								onClick={() =>
									act(
										() => api.reviewAnswer(gate.id, opt, comment.trim()),
										gate.id,
									)
								}
							>
								{opt}
							</Button>
						))}
					</div>
					{gate.free_text_allowed && (
						<div className="mt-2 flex gap-2">
							<Input
								value={text}
								onChange={(e) => setText(e.target.value)}
								placeholder="Or type an answer…"
								className="flex-1"
							/>
							<Button
								type="button"
								size="sm"
								disabled={busy || !text}
								onClick={() =>
									act(
										() => api.reviewAnswer(gate.id, text, comment.trim()),
										gate.id,
									)
								}
							>
								Answer
							</Button>
						</div>
					)}
				</>
			)}
		</Card>
	);
}
