"use client";

import { Card, CardTitle } from "@/components/card";
import { api } from "@/lib/api";
import type { ReviewGate } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useCallback, useState } from "react";

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
			<h2 className="text-xl font-bold mb-4">Gates</h2>
			<p className="text-sm mb-4" style={{ color: "var(--fg-muted)" }}>
				Harness runs paused for a human decision — permission approvals and
				input questions. Resolving here is the same action as{" "}
				<code>swarmkit review</code>.
			</p>

			{loading && <p className="text-sm opacity-50">Loading…</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{data && data.length === 0 && (
				<p className="text-sm" style={{ color: "var(--fg-muted)" }}>
					No pending gates.
				</p>
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
				<span
					className="rounded px-1.5 py-0.5 text-[10px] uppercase"
					style={{
						background: "var(--bg)",
						color: "var(--accent)",
						border: "1px solid var(--accent)",
					}}
				>
					{gate.kind}
				</span>
				<CardTitle>{gate.agent_id}</CardTitle>
				<span
					className="ml-auto font-mono text-xs"
					style={{ color: "var(--fg-muted)" }}
				>
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
						<button
							type="button"
							disabled={busy}
							onClick={() => act(() => api.reviewApprove(gate.id), gate.id)}
							className="rounded px-3 py-1 text-sm"
							style={{ background: "var(--accent)", color: "var(--bg)" }}
						>
							Approve
						</button>
						<button
							type="button"
							disabled={busy}
							onClick={() => act(() => api.reviewReject(gate.id), gate.id)}
							className="rounded px-3 py-1 text-sm"
							style={{
								border: "1px solid var(--error)",
								color: "var(--error)",
							}}
						>
							Reject
						</button>
					</div>
				</>
			)}

			{gate.kind === "input" && (
				<>
					<p className="mt-2 text-sm">{gate.question}</p>
					<div className="mt-2 flex flex-wrap gap-2">
						{gate.options.map((opt) => (
							<button
								type="button"
								key={opt}
								disabled={busy}
								onClick={() =>
									act(() => api.reviewAnswer(gate.id, opt), gate.id)
								}
								className="rounded px-3 py-1 text-sm"
								style={{
									border: "1px solid var(--accent)",
									color: "var(--accent)",
								}}
							>
								{opt}
							</button>
						))}
					</div>
					{gate.free_text_allowed && (
						<div className="mt-2 flex gap-2">
							<input
								value={text}
								onChange={(e) => setText(e.target.value)}
								placeholder="Or type an answer…"
								className="flex-1 rounded px-2 py-1 text-sm"
								style={{
									background: "var(--bg)",
									border: "1px solid var(--border)",
								}}
							/>
							<button
								type="button"
								disabled={busy || !text}
								onClick={() =>
									act(() => api.reviewAnswer(gate.id, text), gate.id)
								}
								className="rounded px-3 py-1 text-sm"
								style={{ background: "var(--accent)", color: "var(--bg)" }}
							>
								Answer
							</button>
						</div>
					)}
				</>
			)}
		</Card>
	);
}
