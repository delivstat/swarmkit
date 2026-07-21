"use client";

// The Pipelines surface (design/details/pipeline-controller.md): author/inspect StageGraph artifacts —
// the pipeline as data. A controller sequences the stages as a saga; here we edit the definition
// (metadata + stages + loops) and visualize the wiring. Mirrors how funnels are surfaced (list +
// editor), but the primary editor is the whole-artifact schema FORM (+ raw YAML) — the canvas is a
// READ-ONLY DAG view, because a stage graph is wired by signal (`success` → `when`), not drawn.
//
// Defensive by design: the runtime already serves the stage-graph schema (GET /api/schema/stage-graph)
// and `@swarmkit/schema` validates locally, but the pipeline CRUD routes (/pipelines,
// /api/pipelines/{id}) may not be wired yet — the list gates gracefully to empty and a save the
// runtime rejects surfaces a clear "not persisted" notice rather than failing silently.

import { dump, load } from "js-yaml";
import { Boxes, Funnel, Lock, Plus, Undo2, Workflow, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { SchemaForm } from "@/components/schema-form";
import { StageGraphCanvas } from "@/components/stage-graph-canvas";
import { StageGraphEditor } from "@/components/stage-graph-editor";
import { StageInspector } from "@/components/stage-inspector";
import { StagePalette } from "@/components/stage-palette";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { JsonSchema } from "@/lib/schema-form";
import { readStages, stageGraphToGraph } from "@/lib/stage-graph";
import {
	addLoop,
	addStage,
	deleteLoop,
	deleteSignalEdge,
	drawSignalEdge,
	removeStage,
	removeWhenEvent,
} from "@/lib/stage-graph-edit";
import { useRefOptions } from "@/lib/use-ref-options";
import { cn } from "@/lib/utils";

const NEW_STAGE_GRAPH_TEMPLATE = `apiVersion: swarmkit/v1
kind: StageGraph
metadata:
  id: NAME
  name: ""
  description: "Pipeline as data — a controller sequences these stages as a saga."
stages:
  - id: intake
    topology: ""
    when: []
    success: ""
loops: []
provenance:
  authored_by: human
  version: 1.0.0
`;

type EditorMode = "canvas" | "form" | "yaml";

function NewPipelineDialog({
	onClose,
	onCreate,
}: {
	onClose: () => void;
	onCreate: (id: string, obj: Record<string, unknown>) => void;
}) {
	const [name, setName] = useState("");
	const [error, setError] = useState<string | null>(null);

	const handleCreate = () => {
		const slug = name
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-|-$/g, "");
		if (!slug) {
			setError("Name must contain at least one letter");
			return;
		}
		try {
			const obj = load(
				NEW_STAGE_GRAPH_TEMPLATE.replace("NAME", slug),
			) as Record<string, unknown>;
			onCreate(slug, obj);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		}
	};

	return (
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>New Pipeline</DialogTitle>
				</DialogHeader>
				<div className="space-y-1.5">
					<Label htmlFor="pipeline-name">Pipeline ID (kebab-case)</Label>
					<Input
						id="pipeline-name"
						placeholder="sdlc-pipeline"
						value={name}
						onChange={(e) => setName(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === "Enter") handleCreate();
						}}
					/>
					{error && <p className="text-xs text-destructive">{error}</p>}
				</div>
				<DialogFooter>
					<Button type="button" variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button type="button" onClick={handleCreate} disabled={!name.trim()}>
						Create
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

/** The read-only detail panel for the stage selected in the canvas. The canvas visualizes wiring;
 * config is edited in the form/yaml views, so this only *reads* the selected stage's fields. */
function StageDetail({
	obj,
	selectedStage,
}: {
	obj: Record<string, unknown>;
	selectedStage: string | null;
}) {
	if (!selectedStage) {
		return (
			<div className="p-4 text-sm text-muted-foreground">
				Click a stage to inspect it. Editing happens in the form or yaml view.
			</div>
		);
	}
	const stage = readStages(obj).find((s) => s.id === selectedStage);
	if (!stage) {
		return (
			<div className="p-4 text-sm text-muted-foreground">
				Stage <span className="font-medium">{selectedStage}</span> is no longer
				in the graph.
			</div>
		);
	}
	const isEntry =
		stageGraphToGraph(obj).nodes.find((n) => n.id === selectedStage)?.data
			.isEntry ?? false;
	return (
		<div className="space-y-3 p-4 text-sm">
			<div className="flex items-center gap-2">
				<Boxes size={15} className="text-sky-500" />
				<span className="font-semibold">{stage.id}</span>
				{isEntry ? <Badge variant="secondary">entry</Badge> : null}
			</div>
			<Field label="Topology" value={stage.topology ?? "—"} />
			<Field
				label="When (entry events)"
				value={stage.when.length ? stage.when.join(", ") : "—"}
			/>
			<Field label="Success signal" value={stage.success ?? "—"} />
			{stage.gate ? (
				<Field
					label="Gate"
					value={stage.gate}
					icon={<Funnel size={12} className="text-muted-foreground" />}
				/>
			) : null}
			{stage.locks.length ? (
				<Field
					label="Locks"
					value={stage.locks.join(", ")}
					icon={<Lock size={12} className="text-muted-foreground" />}
				/>
			) : null}
			{stage.releaseLocksOn ? (
				<Field label="Release locks on" value={stage.releaseLocksOn} />
			) : null}
			{stage.compensation ? (
				<Field
					label="Compensation"
					value={stage.compensation}
					icon={<Undo2 size={12} className="text-muted-foreground" />}
				/>
			) : null}
		</div>
	);
}

function Field({
	label,
	value,
	icon,
}: {
	label: string;
	value: string;
	icon?: React.ReactNode;
}) {
	return (
		<div>
			<div className="flex items-center gap-1 text-xs uppercase tracking-wide text-muted-foreground">
				{icon}
				{label}
			</div>
			<div className="font-mono text-xs">{value}</div>
		</div>
	);
}

export default function PipelinesPage() {
	const [schema, setSchema] = useState<JsonSchema | null>(null);
	const [names, setNames] = useState<string[]>([]);
	const [listUnavailable, setListUnavailable] = useState(false);
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [obj, setObj] = useState<Record<string, unknown>>({});
	const [savedObj, setSavedObj] = useState<Record<string, unknown> | null>(
		null,
	);
	const [yamlDraft, setYamlDraft] = useState("");
	const [mode, setMode] = useState<EditorMode>("form");
	const [selectedStage, setSelectedStage] = useState<string | null>(null);
	// Canvas sub-mode: read-only DAG (view) vs. the editing surface (edit). The read-only view is
	// always available (design/details/pipeline-editor-canvas.md — three views of one document).
	const [canvasEditable, setCanvasEditable] = useState(false);
	const [showNew, setShowNew] = useState(false);
	const [saving, setSaving] = useState(false);
	const [notice, setNotice] = useState<string | null>(null);
	const refOptions = useRefOptions();

	// The stage-graph schema drives the form. The runtime serves it at GET /api/schema/stage-graph
	// (SchemaName includes "stage-graph"); a failure leaves the form gated.
	useEffect(() => {
		api
			.schema("stage-graph")
			.then((s) => setSchema(s as JsonSchema))
			.catch(() => setSchema(null));
	}, []);

	const loadList = useCallback(() => {
		api
			.pipelines()
			.then((n) => {
				setNames(n);
				setListUnavailable(false);
			})
			.catch(() => {
				setNames([]);
				setListUnavailable(true);
			});
	}, []);
	useEffect(() => loadList(), [loadList]);

	const dirty = useMemo(
		() => savedObj !== null && dump(obj) !== dump(savedObj),
		[obj, savedObj],
	);

	const openPipeline = (id: string, next: Record<string, unknown>) => {
		setSelectedId(id);
		setObj(next);
		setSavedObj(next);
		setYamlDraft(dump(next));
		setSelectedStage(null);
		setNotice(null);
	};

	const loadPipeline = (id: string) => {
		api
			.stageGraphYaml(id)
			.then((r) => {
				const parsed = (load(r.yaml) as Record<string, unknown>) ?? {};
				openPipeline(id, parsed);
			})
			.catch((err) =>
				setNotice(
					`Could not load pipeline "${id}" (the runtime may not serve /api/pipelines yet): ${
						err instanceof Error ? err.message : String(err)
					}`,
				),
			);
	};

	// Keep obj as the source of truth; sync yaml when entering/leaving YAML mode.
	const setModeSynced = (next: EditorMode) => {
		if (next === "yaml") {
			setYamlDraft(dump(obj));
		} else if (mode === "yaml") {
			try {
				setObj((load(yamlDraft) as Record<string, unknown>) ?? {});
			} catch {
				setNotice("YAML is invalid — fix it before switching views.");
				return;
			}
		}
		setNotice(null);
		setMode(next);
	};

	// Canvas gestures apply a pure mutation (lib/stage-graph-edit.ts) to the authoritative document.
	// The YAML/form views stay in lockstep because they all read `obj`.
	const mutate = (next: Record<string, unknown>) => setObj(next);

	// FOLLOW-UP (design/details/pipeline-editor-canvas.md §Save & governance): editing a pipeline is a
	// `topologies:modify`-class act, so save should route through the growth-loop propose → approve
	// path (a diffed, human-approved change) rather than a silent write. That governance path is not
	// wired yet; for v1 we use the existing validated `api.saveStageGraph`.
	const handleSave = async () => {
		const source = mode === "yaml" ? safeParse(yamlDraft) : obj;
		if (!source) {
			setNotice("YAML is invalid — cannot save.");
			return;
		}
		const meta = (source.metadata as Record<string, unknown> | undefined) ?? {};
		const id = (selectedId ?? (meta.id as string | undefined)) || "";
		if (!id) {
			setNotice("Pipeline metadata.id is required to save.");
			return;
		}
		setSaving(true);
		setNotice(null);
		try {
			const result = await api.saveStageGraph(id, dump(source));
			if (result.valid) {
				await api.reloadWorkspace().catch(() => undefined);
				setObj(source);
				setSavedObj(source);
				setSelectedId(id);
				loadList();
				setNotice("Saved.");
			} else {
				setNotice(
					result.errors?.map((e) => e.message).join(", ") ??
						"The runtime rejected the stage graph.",
				);
			}
		} catch (err) {
			// The most likely cause is that the pipeline CRUD route is not wired in this runtime yet.
			setNotice(
				`The stage graph is schema-valid, but the runtime did not accept the write (PUT /api/pipelines/${id}). The pipeline CRUD routes may not be wired yet: ${
					err instanceof Error ? err.message : String(err)
				}`,
			);
		} finally {
			setSaving(false);
		}
	};

	const hasPipelineOpen = savedObj !== null;
	const stageCount = useMemo(() => readStages(obj).length, [obj]);

	return (
		<div className="-m-6 flex h-[calc(100vh-3rem)] flex-col">
			{/* Header */}
			<div className="flex shrink-0 items-center gap-3 border-b px-4 py-2">
				<Workflow size={18} className="text-sky-500" />
				<span className="font-semibold">Pipelines</span>
				<Button type="button" size="sm" onClick={() => setShowNew(true)}>
					<Plus size={12} /> New
				</Button>
				{hasPipelineOpen && (
					<>
						<span className="text-xs text-muted-foreground">{selectedId}</span>
						<Badge variant="secondary">
							<Zap size={10} /> {stageCount} stage
							{stageCount === 1 ? "" : "s"}
						</Badge>
						{dirty && <Badge variant="warning">unsaved</Badge>}
					</>
				)}
				{hasPipelineOpen && (
					<div className="ml-auto flex overflow-hidden rounded-md border">
						{(["canvas", "form", "yaml"] as const).map((m) => (
							<button
								key={m}
								type="button"
								onClick={() => setModeSynced(m)}
								className={cn(
									"px-3 py-1 text-xs capitalize transition-colors",
									mode === m
										? "bg-accent font-medium text-accent-foreground"
										: "text-muted-foreground hover:bg-accent/50",
								)}
							>
								{m}
							</button>
						))}
					</div>
				)}
				{hasPipelineOpen && (
					<Button
						type="button"
						size="sm"
						onClick={handleSave}
						disabled={saving}
					>
						{saving ? "Saving…" : "Save"}
					</Button>
				)}
			</div>

			{notice && (
				<div className="shrink-0 border-b bg-muted px-4 py-1.5 text-xs text-muted-foreground">
					{notice}
				</div>
			)}

			<div className="flex flex-1 overflow-hidden">
				{/* Left: pipeline list */}
				<div className="w-56 shrink-0 overflow-y-auto border-r p-2">
					{listUnavailable && (
						<p className="px-2 py-2 text-xs text-muted-foreground">
							The runtime does not serve <code>/pipelines</code> yet. You can
							still author and validate a new pipeline here.
						</p>
					)}
					{!listUnavailable && names.length === 0 && (
						<p className="px-2 py-2 text-xs text-muted-foreground">
							No pipelines yet. Create one with New.
						</p>
					)}
					{names.map((name) => (
						<button
							key={name}
							type="button"
							onClick={() => loadPipeline(name)}
							className={cn(
								"flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
								selectedId === name
									? "bg-accent font-medium text-accent-foreground"
									: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
							)}
						>
							<Workflow size={13} />
							{name}
						</button>
					))}
				</div>

				{/* Right: editor */}
				<div className="flex flex-1 flex-col overflow-hidden">
					{!hasPipelineOpen && (
						<div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
							Select a pipeline, or create one with New.
						</div>
					)}

					{hasPipelineOpen && mode === "canvas" && (
						<div className="flex flex-1 overflow-hidden">
							{canvasEditable && (
								<StagePalette
									topologies={refOptions.topology ?? []}
									onAddStage={(t) => {
										const next = addStage(obj, t);
										mutate(next);
									}}
								/>
							)}
							<div className="flex flex-1 flex-col overflow-hidden">
								<div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs">
									<div className="flex overflow-hidden rounded-md border">
										{(["view", "edit"] as const).map((v) => {
											const on = (v === "edit") === canvasEditable;
											return (
												<button
													key={v}
													type="button"
													onClick={() => setCanvasEditable(v === "edit")}
													className={cn(
														"px-2 py-0.5 text-xs capitalize transition-colors",
														on
															? "bg-accent font-medium text-accent-foreground"
															: "text-muted-foreground hover:bg-accent/50",
													)}
												>
													{v}
												</button>
											);
										})}
									</div>
									<span className="text-muted-foreground">
										{canvasEditable
											? "drag right→left handles to wire a signal · top→top for a loop · Delete removes · click a stage to configure"
											: "read-only · click a stage to inspect"}
									</span>
								</div>
								<div className="relative min-h-[240px] flex-1 border-b">
									{canvasEditable ? (
										<StageGraphEditor
											graph={obj}
											refOptions={refOptions}
											selectedStage={selectedStage}
											onSelectStage={setSelectedStage}
											editable
											onAddStage={(t) => mutate(addStage(obj, t))}
											onDrawSignal={(s, t) => mutate(drawSignalEdge(obj, s, t))}
											onAddLoop={(t, w) => mutate(addLoop(obj, t, w))}
											onDeleteSignal={(s, t) =>
												mutate(deleteSignalEdge(obj, s, t))
											}
											onDeleteLoop={(t, w) => mutate(deleteLoop(obj, t, w))}
											onRemoveStage={(id) => {
												mutate(removeStage(obj, id));
												if (selectedStage === id) setSelectedStage(null);
											}}
											onRemoveExternalEntry={(st, ev) =>
												mutate(removeWhenEvent(obj, st, ev))
											}
										/>
									) : (
										<StageGraphCanvas
											graph={obj}
											selectedStage={selectedStage}
											onSelectStage={setSelectedStage}
										/>
									)}
								</div>
								<div className="flex h-[42%] shrink-0 flex-col overflow-hidden">
									<div className="flex-1 overflow-y-auto">
										{canvasEditable ? (
											<StageInspector
												doc={obj}
												stageId={selectedStage}
												refOptions={refOptions}
												onChange={mutate}
												onRenamed={setSelectedStage}
												onDeleted={() => setSelectedStage(null)}
											/>
										) : (
											<StageDetail obj={obj} selectedStage={selectedStage} />
										)}
									</div>
								</div>
							</div>
						</div>
					)}

					{hasPipelineOpen && mode === "form" && (
						<div className="flex-1 overflow-y-auto p-4">
							{schema ? (
								<SchemaForm
									schema={schema}
									value={obj}
									onChange={setObj}
									options={refOptions}
								/>
							) : (
								<p className="text-sm text-muted-foreground">
									Loading stage-graph schema…
								</p>
							)}
						</div>
					)}

					{hasPipelineOpen && mode === "yaml" && (
						<Textarea
							className="flex-1 resize-none rounded-none border-0 font-mono text-xs focus-visible:ring-0"
							value={yamlDraft}
							onChange={(e) => setYamlDraft(e.target.value)}
							spellCheck={false}
						/>
					)}
				</div>
			</div>

			{showNew && (
				<NewPipelineDialog
					onClose={() => setShowNew(false)}
					onCreate={(id, next) => {
						setShowNew(false);
						setMode("form");
						openPipeline(id, next);
					}}
				/>
			)}
		</div>
	);
}

function safeParse(text: string): Record<string, unknown> | null {
	try {
		return (load(text) as Record<string, unknown>) ?? {};
	} catch {
		return null;
	}
}
