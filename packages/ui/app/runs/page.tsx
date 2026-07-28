"use client";

// The pipeline Runs surface (design/details/bundled-pipeline-orchestrator.md §4): the read half of
// the bundled orchestrator. List + SEARCH pipeline runs (by correlation id, filter by status), and
// on select open the run detail — a READ-ONLY replay canvas (the StageGraph coloured by this run's
// per-stage status) plus a node inspector (timeline, produced artifact, the approval trail). It is a
// thin layer over serve's saga read endpoints; a workspace with no orchestrator store gates to an
// empty, explained state. Read-only — dispatch + gate acts live in the CLI (`swarmkit pipeline`).

import { load } from "js-yaml";
import { Search, Workflow } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { RunNodeInspector } from "@/components/run-node-inspector";
import { RunReplayCanvas } from "@/components/run-replay-canvas";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { RUN_STAGE_META } from "@/lib/run-status";
import type { StageGraphDoc } from "@/lib/stage-graph";
import type { SagaDetail, SagaStatus, SagaSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_COLOR: Record<SagaStatus, string> = {
	active: "#0ea5e9",
	parked: "#f59e0b",
	completed: "#22c55e",
	rejected: "#ef4444",
	failed: "#ef4444",
};

function RunStatusBadge({ status }: { status: SagaStatus }) {
	const color = STATUS_COLOR[status] ?? "#94a3b8";
	return (
		<Badge variant="outline" style={{ borderColor: color, color }}>
			{status}
		</Badge>
	);
}

export default function RunsPage() {
	const [query, setQuery] = useState("");
	const [status, setStatus] = useState("all");
	const [runs, setRuns] = useState<SagaSummary[]>([]);
	const [listError, setListError] = useState<string | null>(null);
	const [selectedId, setSelectedId] = useState<string | null>(null);

	// The search list polls: a live run advances on its own as the orchestrator drives it.
	const loadRuns = useCallback(() => {
		api
			.sagas({ q: query, status })
			.then((r) => {
				setRuns(r.sagas);
				setListError(null);
			})
			.catch((e) => {
				setRuns([]);
				setListError(String(e));
			});
	}, [query, status]);

	useEffect(() => {
		loadRuns();
		const id = setInterval(loadRuns, 5000);
		return () => clearInterval(id);
	}, [loadRuns]);

	return (
		<div className="flex h-[calc(100vh-3.5rem)]">
			{/* ── Left: search + run list ─────────────────────────────────────────── */}
			<aside className="flex w-80 shrink-0 flex-col border-r">
				<div className="flex flex-col gap-2 border-b p-3">
					<div className="flex items-center gap-2">
						<Workflow className="h-4 w-4 text-muted-foreground" />
						<h1 className="text-sm font-semibold">Pipeline runs</h1>
					</div>
					<div className="relative">
						<Search className="absolute left-2 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
						<Input
							value={query}
							onChange={(e) => setQuery(e.target.value)}
							placeholder="Search by correlation id…"
							className="pl-7"
						/>
					</div>
					<Select value={status} onValueChange={setStatus}>
						<SelectTrigger className="h-8 text-xs">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">All statuses</SelectItem>
							<SelectItem value="active">Active</SelectItem>
							<SelectItem value="completed">Completed</SelectItem>
						</SelectContent>
					</Select>
				</div>
				<div className="flex-1 overflow-y-auto">
					{listError ? (
						<p className="p-3 text-xs text-muted-foreground">
							No pipeline runs to show. This workspace may have no orchestrator
							store yet.
						</p>
					) : runs.length === 0 ? (
						<p className="p-3 text-xs text-muted-foreground">No runs match.</p>
					) : (
						<ul>
							{runs.map((r) => (
								<li key={r.correlation_id}>
									<button
										type="button"
										onClick={() => setSelectedId(r.correlation_id)}
										className={cn(
											"flex w-full flex-col gap-1 border-b px-3 py-2 text-left hover:bg-muted/50",
											selectedId === r.correlation_id && "bg-muted",
										)}
									>
										<div className="flex items-center justify-between gap-2">
											<span className="truncate font-mono text-xs">
												{r.correlation_id}
											</span>
											<RunStatusBadge status={r.status} />
										</div>
										<div className="flex items-center gap-2 text-[11px] text-muted-foreground">
											<span className="truncate">{r.graph}</span>
											{r.tag ? (
												<span className="truncate">· {r.tag}</span>
											) : null}
										</div>
									</button>
								</li>
							))}
						</ul>
					)}
				</div>
			</aside>

			{/* ── Right: run detail (replay canvas + node inspector) ──────────────── */}
			<main className="flex min-w-0 flex-1 flex-col">
				{selectedId ? (
					<RunDetail correlationId={selectedId} />
				) : (
					<div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
						Select a run to replay its pipeline.
					</div>
				)}
			</main>
		</div>
	);
}

function RunDetail({ correlationId }: { correlationId: string }) {
	const [saga, setSaga] = useState<SagaDetail | null>(null);
	const [doc, setDoc] = useState<StageGraphDoc>(null);
	const [error, setError] = useState<string | null>(null);
	const [selectedStage, setSelectedStage] = useState<string | null>(null);

	// Poll the run so the canvas replays live progress; refetch when the selection changes.
	useEffect(() => {
		let live = true;
		const load = () =>
			api
				.saga(correlationId)
				.then((s) => {
					if (!live) return;
					setSaga(s);
					setError(null);
				})
				.catch((e) => live && setError(String(e)));
		load();
		const id = setInterval(load, 5000);
		return () => {
			live = false;
			clearInterval(id);
		};
	}, [correlationId]);

	// The run names its StageGraph; fetch the definition once to lay out the replay canvas.
	const graph = saga?.graph;
	useEffect(() => {
		if (!graph) return;
		let live = true;
		api
			.stageGraphYaml(graph)
			.then((r) => live && setDoc(load(r.yaml) as StageGraphDoc))
			.catch(() => live && setDoc(null));
		return () => {
			live = false;
		};
	}, [graph]);

	// Default the inspector to the stage the run is currently on / parked at.
	const focusStage = useMemo(
		() =>
			selectedStage ?? saga?.pending_gate_stage ?? saga?.current_stage ?? null,
		[selectedStage, saga],
	);

	if (error) {
		return (
			<div className="flex flex-1 items-center justify-center text-sm text-red-500">
				{error}
			</div>
		);
	}
	if (!saga) {
		return (
			<div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
				Loading run…
			</div>
		);
	}

	return (
		<>
			<header className="flex items-center gap-3 border-b px-4 py-3">
				<span className="font-mono text-sm font-medium">
					{saga.correlation_id}
				</span>
				<RunStatusBadge status={saga.status} />
				<span className="text-xs text-muted-foreground">{saga.graph}</span>
				{saga.tag ? (
					<span className="text-xs text-muted-foreground">· {saga.tag}</span>
				) : null}
				<span className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
					{(["passed", "active", "parked", "failed"] as const).map((s) => (
						<span key={s} className="flex items-center gap-1">
							<span
								className="inline-block h-2 w-2 rounded-full"
								style={{ backgroundColor: RUN_STAGE_META[s].color }}
							/>
							{RUN_STAGE_META[s].label}
						</span>
					))}
				</span>
			</header>
			<div className="flex min-h-0 flex-1">
				<div className="min-w-0 flex-1">
					<RunReplayCanvas
						doc={doc}
						saga={saga}
						selectedStage={focusStage}
						onSelectStage={setSelectedStage}
					/>
				</div>
				<aside className="w-96 shrink-0 overflow-y-auto border-l">
					{focusStage ? (
						<RunNodeInspector saga={saga} stageId={focusStage} />
					) : (
						<p className="p-4 text-sm text-muted-foreground">
							Select a stage on the canvas to inspect its timeline and artifact.
						</p>
					)}
				</aside>
			</div>
		</>
	);
}
