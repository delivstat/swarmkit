"use client";

import { useCallback, useEffect, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Comprehension } from "@/lib/types";

/** Comprehension-debt telemetry — the same signals as `swarmkit comprehension` (GET /comprehension).
 * Read-only, never a gate. The fast-approve threshold is shown pre-set to the active value so the
 * operator sees what is in effect; editing it re-queries the endpoint. */
export default function ComprehensionPage() {
	const [data, setData] = useState<Comprehension | null>(null);
	const [threshold, setThreshold] = useState<number | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);

	const fetchIt = useCallback(async (seconds?: number) => {
		setLoading(true);
		setError(null);
		try {
			const d = await api.getComprehension(seconds);
			setData(d);
			// Pre-set the input to the active threshold on first load; keep the user's edit after.
			setThreshold((cur) => cur ?? d.threshold_seconds);
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		void fetchIt();
	}, [fetchIt]);

	return (
		<div>
			<h2 className="mb-1 text-xl font-bold">Comprehension</h2>
			<p className="mb-4 text-sm text-muted-foreground">
				Read-only signals from the audit log — never a gate, never a score. The
				same data as <code>swarmkit comprehension</code>.
			</p>

			<div className="mb-4 flex items-end gap-2">
				<div className="flex flex-col gap-1">
					<label
						htmlFor="fast-approve"
						className="text-xs text-muted-foreground"
					>
						Fast-approve threshold (seconds)
					</label>
					<Input
						id="fast-approve"
						type="number"
						min={0}
						step={1}
						className="w-40"
						value={threshold ?? ""}
						onChange={(e) =>
							setThreshold(
								e.target.value === "" ? null : Number(e.target.value),
							)
						}
						onKeyDown={(e) => {
							if (e.key === "Enter" && threshold != null)
								void fetchIt(threshold);
						}}
					/>
				</div>
				<Button
					type="button"
					variant="outline"
					disabled={threshold == null || loading}
					onClick={() => {
						if (threshold != null) void fetchIt(threshold);
					}}
				>
					Recompute
				</Button>
			</div>

			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}

			{data && (
				<div className="grid gap-4">
					<Card>
						<CardTitle>Verdict</CardTitle>
						<p className="text-sm">{data.verdict}</p>
						<p className="mt-2 text-xs text-muted-foreground">
							{data.approvals_seen} approval(s) in window · threshold{" "}
							{data.threshold_seconds}s
						</p>
					</Card>

					<Card>
						<CardTitle>Fast approvals</CardTitle>
						{data.fast_approvals.length === 0 ? (
							<p className="text-sm text-muted-foreground">
								None resolved under {data.threshold_seconds}s — no rubber-stamp
								signal.
							</p>
						) : (
							<div className="grid gap-2">
								{data.fast_approvals.map((f) => (
									<div
										key={`${f.run_id}:${f.gate_id}:${f.timestamp}`}
										className="flex items-center gap-2 text-sm"
									>
										<Badge variant="destructive">
											{f.latency_seconds.toFixed(1)}s
										</Badge>
										<span className="font-mono">{f.gate_id}</span>
										<span className="text-muted-foreground">
											· {f.distinct_approvers} approver(s) · run{" "}
											{f.run_id ?? "—"}
										</span>
									</div>
								))}
							</div>
						)}
					</Card>

					<Card>
						<CardTitle>Deferred signals</CardTitle>
						<p className="mb-2 text-xs text-muted-foreground">
							Not yet derivable from the audit log — disclosed, never faked.
						</p>
						<ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
							{data.deferred.map((d) => (
								<li key={d}>{d}</li>
							))}
						</ul>
					</Card>
				</div>
			)}
		</div>
	);
}
