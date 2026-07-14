"use client";

import { useCallback, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { ReviewGate } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

/** Pending harness gates — §6.2 permission approvals and §6.3 input requests — resolved by a human.
 * Same review queue + HTTP API the CLI (`swarmkit review`) and fleet UI use, so a gate resolves
 * identically whichever surface an operator picks. */
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
				Harness runs paused for a human decision — permission approvals and
				input questions. Resolving here is the same action as{" "}
				<code>swarmkit review</code>.
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
					<div className="mt-3 flex gap-2">
						<Button
							type="button"
							size="sm"
							disabled={busy}
							onClick={() => act(() => api.reviewApprove(gate.id), gate.id)}
						>
							Approve
						</Button>
						<Button
							type="button"
							variant="outline"
							size="sm"
							disabled={busy}
							className="border-destructive text-destructive hover:bg-destructive/10"
							onClick={() => act(() => api.reviewReject(gate.id), gate.id)}
						>
							Reject
						</Button>
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
									act(() => api.reviewAnswer(gate.id, opt), gate.id)
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
									act(() => api.reviewAnswer(gate.id, text), gate.id)
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
