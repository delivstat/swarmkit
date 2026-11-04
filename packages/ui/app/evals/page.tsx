"use client";

/**
 * Evals (design/details/eval-on-serve.md §6): list sets, review one, edit it, run it and follow the
 * run, read its report. One route with query views, like `/job` — the portal is a static export, so
 * there are no dynamic segments.
 *
 *   /evals                 every set + recent runs (including runs of deleted sets)
 *   /evals?set=<id>        the set's cases against its recent runs; run it from here
 *   /evals?set=<id>&edit=1 the editor — SchemaForm or YAML, saved through PUT /api/evals/<id>
 *   /evals?new=1           a new set, POST /api/evals
 *   /evals?run=<id>        one run: polled while running, stop / resume, diff and report
 *
 * Everything goes through the serve routes, which go through EvalService and ArtifactService —
 * the same as `swarmkit eval` — so a run started here lists on the CLI and the reverse.
 */

import { dump, load } from "js-yaml";
import { Pencil, Play, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import {
	CaseReview,
	DiffCard,
	EvalRunTable,
	EvalSetTable,
	RunHeader,
	RunReport,
	runHref,
	setHref,
} from "@/components/evals";
import { SchemaForm } from "@/components/schema-form";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import {
	type EvalRunDetail,
	type EvalRunSummary,
	type EvalSet,
	type EvalSetListItem,
	caseHistory,
	fieldErrors,
	followEvalRun,
	newEvalSetYaml,
} from "@/lib/evals";
import type { JsonSchema } from "@/lib/schema-form";
import { usePoll } from "@/lib/use-poll";
import { useRefOptions } from "@/lib/use-ref-options";
import { cn } from "@/lib/utils";

/** How many recent runs the review view scores each case against. */
const HISTORY_RUNS = 5;

function ListView() {
	const fetchSets = useCallback(() => api.evalSets(), []);
	const fetchRuns = useCallback(() => api.evalRuns(undefined, 20), []);
	const sets = usePoll<EvalSetListItem[]>(fetchSets, 10000);
	const runs = usePoll<EvalRunSummary[]>(fetchRuns, 5000);
	const known = useMemo(
		() => new Set((sets.data ?? []).map((s) => s.id)),
		[sets.data],
	);
	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between">
				<h2 className="text-xl font-bold">Evals</h2>
				<Button asChild size="sm">
					<Link href="/evals/?new=1">
						<Plus size={12} /> New eval set
					</Link>
				</Button>
			</div>
			<Card>
				<CardTitle>Eval sets</CardTitle>
				{sets.loading && (
					<p className="text-sm text-muted-foreground">Loading…</p>
				)}
				{sets.error && <p className="text-sm text-destructive">{sets.error}</p>}
				{sets.data && <EvalSetTable sets={sets.data} />}
			</Card>
			<Card>
				<CardTitle>Recent runs</CardTitle>
				{runs.error && <p className="text-sm text-destructive">{runs.error}</p>}
				{runs.data && (
					<EvalRunTable
						runs={runs.data}
						knownSets={sets.data ? known : undefined}
					/>
				)}
			</Card>
		</div>
	);
}

function SetView({ setId }: { setId: string }) {
	const router = useRouter();
	const [set, setSet] = useState<EvalSet | null>(null);
	const [runs, setRuns] = useState<EvalRunSummary[]>([]);
	const [details, setDetails] = useState<EvalRunDetail[]>([]);
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	const load = useCallback(async () => {
		try {
			const [s, rs] = await Promise.all([
				api.evalSet(setId),
				api.evalRuns(setId, 20),
			]);
			setSet(s);
			setRuns(rs);
			// The run list carries no cases; the per-case history needs the recent runs in full.
			setDetails(
				await Promise.all(
					rs.slice(0, HISTORY_RUNS).map((r) => api.evalRun(r.id)),
				),
			);
			setError(null);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		}
	}, [setId]);

	useEffect(() => {
		load();
	}, [load]);

	const history = useMemo(
		() =>
			set
				? caseHistory(
						set.cases.map((c) => c.id),
						details,
					)
				: [],
		[set, details],
	);

	const run = async () => {
		setBusy(true);
		setError(null);
		try {
			const started = await api.runEvalSet(setId);
			router.push(runHref(started.eval_run_id));
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
			setBusy(false);
		}
	};

	const remove = async () => {
		if (!window.confirm(`Delete eval set ${setId}? Its run history is kept.`))
			return;
		try {
			await api.deleteEvalSet(setId);
			router.push("/evals/");
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		}
	};

	const regressed = history.filter((h) => h.regressed).length;
	return (
		<div className="space-y-4">
			<div className="flex items-center gap-3">
				<Link
					href="/evals/"
					className="text-sm text-muted-foreground hover:underline"
				>
					Evals
				</Link>
				<span className="text-muted-foreground">/</span>
				<h2 className="text-xl font-bold">{setId}</h2>
				<div className="ml-auto flex gap-2">
					<Button asChild size="sm" variant="outline">
						<Link href={`${setHref(setId)}&edit=1`}>
							<Pencil size={12} /> Edit
						</Link>
					</Button>
					<Button type="button" size="sm" variant="outline" onClick={remove}>
						<Trash2 size={12} /> Delete
					</Button>
					<Button type="button" size="sm" disabled={busy || !set} onClick={run}>
						<Play size={12} /> {busy ? "Starting…" : "Run"}
					</Button>
				</div>
			</div>
			{error && <p className="text-sm text-destructive">{error}</p>}
			{set && (
				<Card>
					<CardTitle>
						{set.cases.length} cases against{" "}
						<span className="font-mono">{set.target}</span>
						{set.metadata.description ? ` — ${set.metadata.description}` : ""}
						{regressed ? (
							<span className="ml-2 text-destructive">
								· {regressed} regressed
							</span>
						) : null}
					</CardTitle>
					<CaseReview set={set} history={history} />
					<p className="mt-2 text-xs text-muted-foreground">
						{details.length === 1
							? "The dot is the last run."
							: `Dots are the last ${details.length} runs, newest first.`}
					</p>
				</Card>
			)}
			<Card>
				<CardTitle>Runs of this set</CardTitle>
				<EvalRunTable runs={runs} />
			</Card>
		</div>
	);
}

function EditView({ setId }: { setId: string | null }) {
	const router = useRouter();
	const isNew = setId === null;
	const [mode, setMode] = useState<"form" | "yaml">("form");
	const [yaml, setYaml] = useState("");
	const [obj, setObj] = useState<Record<string, unknown>>({});
	const [schema, setSchema] = useState<JsonSchema | null>(null);
	const [byField, setByField] = useState<Record<string, string>>({});
	const [general, setGeneral] = useState<string[]>([]);
	const [saving, setSaving] = useState(false);
	const [loading, setLoading] = useState(true);
	const refOptions = useRefOptions();

	const setBoth = useCallback((text: string) => {
		setYaml(text);
		try {
			setObj((load(text) as Record<string, unknown>) ?? {});
		} catch {
			// keep the form's copy while the YAML draft is unparseable
		}
	}, []);

	useEffect(() => {
		api
			.schema("eval-set")
			.then((s) => setSchema(s as JsonSchema))
			.catch(() => {
				setSchema(null);
				setMode("yaml");
			});
	}, []);

	useEffect(() => {
		if (isNew) {
			api
				.topologies()
				.then((ts) => setBoth(newEvalSetYaml(ts[0] ?? "")))
				.catch(() => setBoth(newEvalSetYaml()))
				.finally(() => setLoading(false));
			return;
		}
		api
			.evalSetYaml(setId)
			.then((r) => setBoth(r.yaml))
			.catch((err) => setGeneral([String(err)]))
			.finally(() => setLoading(false));
	}, [isNew, setId, setBoth]);

	const save = async () => {
		setSaving(true);
		setByField({});
		setGeneral([]);
		try {
			const text = mode === "form" ? dump(obj) : yaml;
			const result = isNew
				? await api.createEvalSet(text)
				: await api.saveEvalSet(setId, text);
			if (result.valid) {
				router.push(setHref(result.id));
				return;
			}
			const errs = fieldErrors(result.errors);
			setByField(errs.byField);
			// In YAML mode there are no fields to put them on.
			setGeneral(
				mode === "form" ? errs.general : result.errors.map((e) => e.message),
			);
		} catch (err) {
			setGeneral([err instanceof Error ? err.message : String(err)]);
		} finally {
			setSaving(false);
		}
	};

	const fieldCount = Object.keys(byField).length;
	return (
		<div className="space-y-4">
			<div className="flex items-center gap-3">
				<Link
					href="/evals/"
					className="text-sm text-muted-foreground hover:underline"
				>
					Evals
				</Link>
				<span className="text-muted-foreground">/</span>
				<h2 className="text-xl font-bold">
					{isNew ? "New eval set" : `Edit ${setId}`}
				</h2>
			</div>
			<Card>
				<div className="mb-3 flex overflow-hidden rounded-md border text-xs w-fit">
					{(["form", "yaml"] as const).map((m) => (
						<button
							key={m}
							type="button"
							disabled={m === "form" && !schema}
							onClick={() => {
								if (m === "yaml") {
									setYaml(dump(obj));
									setMode("yaml");
								} else {
									try {
										setObj((load(yaml) as Record<string, unknown>) ?? {});
										setMode("form");
									} catch {
										setGeneral([
											"The YAML doesn't parse — fix it before using the form.",
										]);
									}
								}
							}}
							className={cn(
								"px-3 py-1 font-medium transition-colors disabled:opacity-40",
								mode === m
									? "bg-accent text-accent-foreground"
									: "text-muted-foreground hover:bg-accent/50",
							)}
						>
							{m === "form" ? "Form" : "YAML"}
						</button>
					))}
				</div>
				{general.map((g) => (
					<p key={g} role="alert" className="mb-2 text-sm text-destructive">
						{g}
					</p>
				))}
				{mode === "form" && fieldCount > 0 ? (
					<p role="alert" className="mb-2 text-sm text-destructive">
						Not saved —{" "}
						{fieldCount === 1 ? "a field needs" : `${fieldCount} fields need`}{" "}
						attention. The file on disk is unchanged.
					</p>
				) : null}
				{loading ? (
					<p className="text-sm text-muted-foreground">Loading…</p>
				) : mode === "form" && schema ? (
					<SchemaForm
						schema={schema}
						value={obj}
						onChange={setObj}
						options={refOptions}
						errors={byField}
					/>
				) : (
					<Textarea
						className="min-h-[420px] font-mono text-xs"
						value={yaml}
						onChange={(e) => setBoth(e.target.value)}
						spellCheck={false}
					/>
				)}
				<div className="mt-4 flex justify-end gap-2">
					<Button asChild variant="outline">
						<Link href={isNew ? "/evals/" : setHref(setId)}>Cancel</Link>
					</Button>
					<Button type="button" onClick={save} disabled={saving || loading}>
						{saving ? "Saving…" : "Save"}
					</Button>
				</div>
			</Card>
		</div>
	);
}

function RunView({ runId }: { runId: string }) {
	const router = useRouter();
	const [run, setRun] = useState<EvalRunDetail | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);
	// Bumped after stop / resume, to start following again — a resumed run is running once more.
	const [follow, setFollow] = useState(0);

	// biome-ignore lint/correctness/useExhaustiveDependencies: `follow` restarts the loop
	useEffect(() => {
		const ctrl = new AbortController();
		followEvalRun(
			() => api.evalRun(runId),
			(r) => {
				setRun(r);
				setError(null);
			},
			{
				signal: ctrl.signal,
				onError: (err) =>
					setError(err instanceof Error ? err.message : String(err)),
			},
		);
		return () => ctrl.abort();
	}, [runId, follow]);

	const act = async (fn: () => Promise<unknown>) => {
		setBusy(true);
		setError(null);
		try {
			await fn();
			setFollow((n) => n + 1);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(false);
		}
	};

	return (
		<div className="space-y-4">
			<div className="flex items-center gap-3">
				<Link
					href="/evals/"
					className="text-sm text-muted-foreground hover:underline"
				>
					Evals
				</Link>
				<span className="text-muted-foreground">/</span>
				{run ? (
					<Link
						href={setHref(run.eval_set_id)}
						className="text-sm hover:underline"
					>
						{run.eval_set_id}
					</Link>
				) : null}
				<span className="text-muted-foreground">/</span>
				<h2 className="font-mono text-lg font-bold">{runId.slice(0, 8)}</h2>
				{run && run.status !== "running" ? (
					<Button
						type="button"
						size="sm"
						variant="outline"
						className="ml-auto"
						disabled={busy}
						onClick={() =>
							act(async () => {
								const started = await api.runEvalSet(run.eval_set_id);
								router.push(runHref(started.eval_run_id));
							})
						}
					>
						<Play size={12} /> Run again
					</Button>
				) : null}
			</div>
			{error && <p className="text-sm text-destructive">{error}</p>}
			{run && (
				<>
					<RunHeader
						run={run}
						busy={busy}
						onStop={() => act(() => api.stopEvalRun(runId))}
						onResume={() => act(() => api.resumeEvalRun(runId))}
					/>
					<DiffCard run={run} />
					<RunReport run={run} />
				</>
			)}
		</div>
	);
}

function EvalsInner() {
	const params = useSearchParams();
	const runId = params.get("run");
	const setId = params.get("set");
	if (runId) return <RunView runId={runId} />;
	if (params.get("new")) return <EditView setId={null} />;
	if (setId && params.get("edit"))
		return <EditView key={setId} setId={setId} />;
	if (setId) return <SetView key={setId} setId={setId} />;
	return <ListView />;
}

export default function EvalsPage() {
	return (
		<Suspense
			fallback={<p className="text-sm text-muted-foreground">Loading…</p>}
		>
			<EvalsInner />
		</Suspense>
	);
}
