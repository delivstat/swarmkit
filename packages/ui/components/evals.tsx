"use client";

/**
 * The Evals page's views (design/details/eval-on-serve.md §6), as plain props-in components so
 * each renders — and is tested — without a serve. `app/evals/page.tsx` fetches, polls and acts.
 */

import { Check, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	type CaseHistory,
	type EvalCaseResult,
	type EvalRunDetail,
	type EvalRunSummary,
	type EvalSet,
	type EvalSetListItem,
	canResumeEval,
	canStopEval,
	expectLines,
	passRate,
	progressLabel,
} from "@/lib/evals";
import { formatUsd } from "@/lib/format";
import { formatWhen } from "@/lib/jobs";
import { cn } from "@/lib/utils";

export const setHref = (id: string) => `/evals/?set=${encodeURIComponent(id)}`;
export const runHref = (id: string) => `/evals/?run=${encodeURIComponent(id)}`;

/** The list: every set with its target, size and how its last run went. */
export function EvalSetTable({ sets }: { sets: EvalSetListItem[] }) {
	if (sets.length === 0) {
		return (
			<p className="text-sm text-muted-foreground">
				No eval sets yet. An eval set is a YAML file under <code>evals/</code> —
				create one here, or with <code>swarmkit eval</code> on the CLI.
			</p>
		);
	}
	return (
		<table className="w-full text-sm">
			<thead className="text-left text-xs text-muted-foreground">
				<tr>
					<th className="py-2 font-medium">Set</th>
					<th className="font-medium">Target</th>
					<th className="font-medium">Cases</th>
					<th className="font-medium">Last run</th>
					<th className="font-medium">Pass rate</th>
					<th className="font-medium">When</th>
				</tr>
			</thead>
			<tbody>
				{sets.map((s) => (
					<tr key={s.id} className="border-t" data-set={s.id}>
						<td className="py-2">
							<Link
								href={setHref(s.id)}
								className="font-medium hover:underline"
							>
								{s.id}
							</Link>
							{s.description ? (
								<div className="text-xs text-muted-foreground">
									{s.description}
								</div>
							) : null}
						</td>
						<td className="font-mono text-xs">{s.target}</td>
						<td>{s.case_count}</td>
						<td>
							{s.last_run ? (
								<Link href={runHref(s.last_run.id)}>
									<StatusBadge status={s.last_run.status} />
								</Link>
							) : (
								<span className="text-xs text-muted-foreground">never run</span>
							)}
						</td>
						<td>
							{s.last_run
								? `${passRate(s.last_run.passed, s.last_run.total)} (${s.last_run.passed}/${s.last_run.total})`
								: "—"}
						</td>
						<td className="text-xs text-muted-foreground">
							{s.last_run ? formatWhen(s.last_run.started_at) : "—"}
						</td>
					</tr>
				))}
			</tbody>
		</table>
	);
}

/** Runs, newest first. `knownSets` marks a run whose set has since been deleted. */
export function EvalRunTable({
	runs,
	knownSets,
}: {
	runs: EvalRunSummary[];
	knownSets?: Set<string>;
}) {
	if (runs.length === 0) {
		return <p className="text-sm text-muted-foreground">No runs yet.</p>;
	}
	return (
		<table className="w-full text-sm">
			<thead className="text-left text-xs text-muted-foreground">
				<tr>
					<th className="py-2 font-medium">Run</th>
					<th className="font-medium">Set</th>
					<th className="font-medium">Status</th>
					<th className="font-medium">Result</th>
					<th className="font-medium">Cost</th>
					<th className="font-medium">From</th>
					<th className="font-medium">Started</th>
				</tr>
			</thead>
			<tbody>
				{runs.map((r) => (
					<tr key={r.id} className="border-t" data-run={r.id}>
						<td className="py-2 font-mono text-xs">
							<Link href={runHref(r.id)} className="hover:underline">
								{r.id.slice(0, 8)}
							</Link>
						</td>
						<td>
							{r.eval_set_id}
							{knownSets && !knownSets.has(r.eval_set_id) ? (
								<Badge variant="outline" className="ml-2">
									deleted set
								</Badge>
							) : null}
						</td>
						<td>
							<StatusBadge status={r.status} />
						</td>
						<td>{progressLabel(r)}</td>
						<td>{formatUsd(r.cost_usd)}</td>
						<td className="text-xs text-muted-foreground">{r.source}</td>
						<td className="text-xs text-muted-foreground">
							{formatWhen(r.started_at)}
						</td>
					</tr>
				))}
			</tbody>
		</table>
	);
}

function ResultDot({ value }: { value: boolean | null }) {
	return (
		<span
			title={value === null ? "not scored" : value ? "passed" : "failed"}
			className={cn(
				"inline-block size-2.5 rounded-full",
				value === null
					? "border border-muted-foreground/40"
					: value
						? "bg-success"
						: "bg-destructive",
			)}
		/>
	);
}

/** A set's cases: input, expectations, and each one's result in recent runs (newest left). */
export function CaseReview({
	set,
	history,
}: {
	set: EvalSet;
	history: CaseHistory[];
}) {
	const byId = new Map(history.map((h) => [h.caseId, h]));
	return (
		<table className="w-full text-sm">
			<thead className="text-left text-xs text-muted-foreground">
				<tr>
					<th className="py-2 font-medium">Case</th>
					<th className="font-medium">Input</th>
					<th className="font-medium">Expects</th>
					<th className="font-medium">Recent runs</th>
				</tr>
			</thead>
			<tbody>
				{set.cases.map((c) => {
					const h = byId.get(c.id);
					return (
						<tr
							key={c.id}
							className={cn(
								"border-t align-top",
								h?.regressed && "bg-destructive/5",
							)}
							data-case={c.id}
						>
							<td className="py-2 pr-3 font-medium">
								{c.id}
								{h?.regressed ? (
									<Badge variant="destructive" className="ml-2">
										regressed
									</Badge>
								) : null}
							</td>
							<td className="max-w-md whitespace-pre-wrap py-2 pr-3 font-mono text-xs">
								{c.input}
							</td>
							<td className="py-2 pr-3 text-xs">
								{expectLines(c.expect).map((line) => (
									<div key={line}>{line}</div>
								))}
							</td>
							<td>
								<div className="flex gap-1 py-2.5">
									{(h?.results ?? []).map((r, i) => (
										// biome-ignore lint/suspicious/noArrayIndexKey: one column per run, fixed order
										<ResultDot key={i} value={r} />
									))}
								</div>
							</td>
						</tr>
					);
				})}
			</tbody>
		</table>
	);
}

/** Status, progress, cost and the controls a run's state allows. */
export function RunHeader({
	run,
	busy,
	onStop,
	onResume,
}: {
	run: EvalRunSummary;
	busy?: boolean;
	onStop?: () => void;
	onResume?: () => void;
}) {
	return (
		<Card>
			<div className="flex flex-wrap items-center gap-3">
				<StatusBadge status={run.status} />
				<span className="text-sm">{progressLabel(run)}</span>
				<span className="text-sm text-muted-foreground">
					{formatUsd(run.cost_usd)}
				</span>
				<span className="text-xs text-muted-foreground">
					{run.target} · from {run.source} · started{" "}
					{formatWhen(run.started_at)}
					{run.finished_at ? ` · finished ${formatWhen(run.finished_at)}` : ""}
				</span>
				<div className="ml-auto flex gap-2">
					{canStopEval(run) && onStop ? (
						<Button
							type="button"
							size="sm"
							variant="outline"
							disabled={busy}
							onClick={onStop}
						>
							Stop after this case
						</Button>
					) : null}
					{run.status === "running" && run.stop_requested_at ? (
						<span className="text-xs text-muted-foreground">
							stopping after the case in flight…
						</span>
					) : null}
					{canResumeEval(run) && onResume ? (
						<Button type="button" size="sm" disabled={busy} onClick={onResume}>
							Resume ({run.total - run.recorded} left)
						</Button>
					) : null}
				</div>
			</div>
			{run.error ? (
				<p className="mt-2 text-xs text-destructive">{run.error}</p>
			) : null}
		</Card>
	);
}

/** What moved against the previous finished run of the same set. */
export function DiffCard({ run }: { run: EvalRunDetail }) {
	const d = run.diff;
	if (!d) {
		return (
			<Card>
				<CardTitle>Against the previous run</CardTitle>
				<p className="text-sm text-muted-foreground">
					{run.status === "running"
						? "The diff is taken when the run finishes."
						: "No earlier finished run of this set to compare with."}
				</p>
			</Card>
		);
	}
	const row = (label: string, ids: string[], tone: string) => (
		<div className="flex items-baseline gap-2 text-sm" data-diff={label}>
			<span className={cn("w-20 font-medium", tone)}>{label}</span>
			<span className="font-mono text-xs">
				{ids.length ? ids.join(", ") : "—"}
			</span>
		</div>
	);
	return (
		<Card>
			<CardTitle>
				Against{" "}
				{d.prior_eval_run_id ? (
					<Link
						href={runHref(d.prior_eval_run_id)}
						className="font-mono underline"
					>
						{d.prior_eval_run_id.slice(0, 8)}
					</Link>
				) : (
					"the previous report file"
				)}{" "}
				· {Math.round(d.prev_pass_rate * 100)}% →{" "}
				{Math.round(d.curr_pass_rate * 100)}%
			</CardTitle>
			{row("regressed", d.regressed, "text-destructive")}
			{row("fixed", d.fixed, "text-success")}
			{row("new", d.new, "text-muted-foreground")}
		</Card>
	);
}

function CaseResultRow({
	result,
	regressed,
}: {
	result: EvalCaseResult;
	regressed: boolean;
}) {
	const [open, setOpen] = useState(!result.passed);
	return (
		<li className="border-t py-2" data-result={result.case_id}>
			<button
				type="button"
				className="flex w-full items-center gap-2 text-left text-sm"
				onClick={() => setOpen((o) => !o)}
			>
				<Badge variant={result.passed ? "success" : "destructive"}>
					{result.passed ? "PASS" : "FAIL"}
				</Badge>
				<span className="font-medium">{result.case_id}</span>
				{regressed ? <Badge variant="outline">regressed</Badge> : null}
				<span className="ml-auto text-xs text-muted-foreground">
					{formatUsd(result.cost_usd)}
				</span>
			</button>
			{open ? (
				<div className="mt-2 space-y-2 pl-2">
					{result.error ? (
						<p className="text-xs text-destructive">{result.error}</p>
					) : null}
					<ul className="space-y-0.5 text-xs">
						{result.checks.map((ck) => (
							<li key={ck.name} className="flex gap-2">
								{ck.passed ? (
									<Check
										className="mt-0.5 size-3 shrink-0 text-success"
										aria-label="passed"
									/>
								) : (
									<X
										className="mt-0.5 size-3 shrink-0 text-destructive"
										aria-label="failed"
									/>
								)}
								<span className="font-mono">{ck.name}</span>
								{ck.detail ? (
									<span className="text-muted-foreground">{ck.detail}</span>
								) : null}
							</li>
						))}
					</ul>
					{result.output ? (
						<pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap">
							{result.output}
						</pre>
					) : null}
				</div>
			) : null}
		</li>
	);
}

/** Per-case results with their checks, in the set's order; cases not yet scored are listed too. */
export function RunReport({ run }: { run: EvalRunDetail }) {
	const scored = new Map(run.report.cases.map((c) => [c.case_id, c]));
	const regressed = new Set(run.diff?.regressed ?? []);
	const pending = (run.eval_set?.cases ?? [])
		.map((c) => c.id)
		.filter((id) => !scored.has(id));
	return (
		<Card>
			<CardTitle>Cases</CardTitle>
			<ul>
				{run.report.cases.map((c) => (
					<CaseResultRow
						key={c.case_id}
						result={c}
						regressed={regressed.has(c.case_id)}
					/>
				))}
				{pending.map((id, i) => (
					<li
						key={id}
						className="flex items-center gap-2 border-t py-2 text-sm text-muted-foreground"
						data-pending={id}
					>
						<Badge variant="outline">
							{run.status === "running" && i === 0 ? "running" : "not run"}
						</Badge>
						{id}
					</li>
				))}
			</ul>
		</Card>
	);
}
