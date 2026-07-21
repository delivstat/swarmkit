"use client";

// The pipeline EDITOR canvas (design/details/pipeline-editor-canvas.md): upgrades the read-only
// StageGraphCanvas into a visual editor. The load-bearing idea — **connections are events, not
// pointers** — means every canvas gesture is a pure mutation of the StageGraph document (see
// lib/stage-graph-edit.ts), and the YAML stays authoritative:
//
//  1. Signal edge (drag a stage's right handle → another's left): ensure A.success = S, add S to
//     B.when. Deleting removes it (and clears A.success if now unmatched). Fan-out = one success in
//     many `when`.
//  2. External entry (a `when` event no stage emits): rendered as a DISTINCT inbound pin (dashed
//     "external"), never a stage-to-stage arrow — there is no source box.
//  3. Loop edge (drag a stage's TOP handle → an upstream stage's top): append `loops: {when, to}`,
//     an explicit back-edge; the trigger event is editable in the inspector.
//
// Live validation (lib/stage-graph-validate.ts) is rendered inline on the nodes. Read-only mode
// (editable=false) reproduces the old canvas so the page can offer view-vs-edit.

import "@xyflow/react/dist/style.css";
import type { RefOptions } from "@/lib/schema-form";
import {
	type ExternalEntry,
	type StageGraphDoc,
	type StageLoop,
	type StageNodeData,
	externalEntries,
	readStages,
	stageGraphToGraph,
} from "@/lib/stage-graph";
import {
	type StageIssue,
	issuesByStage,
	validateStageGraph,
} from "@/lib/stage-graph-validate";
import {
	Background,
	Controls,
	type Edge,
	Handle,
	MarkerType,
	MiniMap,
	type Node,
	type NodeProps,
	Position,
	ReactFlow,
	type ReactFlowProps,
	useEdgesState,
	useNodesState,
} from "@xyflow/react";
import {
	AlertTriangle,
	Boxes,
	Funnel,
	Lock,
	RotateCcw,
	Undo2,
	Webhook,
} from "lucide-react";
import { useEffect, useMemo } from "react";

/** dataTransfer MIME the stage palette and the canvas drop handler share (topology → a new stage). */
export const STAGE_PALETTE_MIME = "application/swarmkit-stage";

const SKY = "#0ea5e9";

interface CanvasStageData extends StageNodeData {
	selected?: boolean;
	editable?: boolean;
	issues?: StageIssue[];
}
type StageFlowNode = Node<CanvasStageData, "stage">;

/** A stage card — the editable node. Entry stages / the selection ring in sky; a validation badge
 * (error → destructive, else warning) summarizes inline issues. Four handles: left/right drive
 * signal edges, top drives loop (back-edge) edges. */
function StageCard({ data }: NodeProps<StageFlowNode>) {
	const issues = data.issues ?? [];
	const hasError = issues.some((i) => i.level === "error");
	const hasWarn = issues.length > 0;
	const accent = data.selected || data.isEntry ? SKY : "var(--border)";
	return (
		<div
			className="min-w-[170px] rounded-lg border-2 bg-card px-3 py-2 text-xs text-card-foreground shadow-sm"
			style={{ borderColor: accent }}
		>
			<Handle
				id="in"
				type="target"
				position={Position.Left}
				isConnectable={data.editable}
				style={{ background: "var(--border)" }}
			/>
			<Handle
				id="loop-in"
				type="target"
				position={Position.Top}
				isConnectable={data.editable}
				style={{ background: "var(--border)" }}
			/>
			<div className="flex items-center gap-1.5 font-medium">
				<Boxes size={14} style={{ color: SKY }} />
				<span>{data.id}</span>
				<span className="ml-auto flex items-center gap-1 text-muted-foreground">
					{data.locks.length > 0 ? (
						<Lock size={11} aria-label={`holds ${data.locks.length} lock(s)`} />
					) : null}
					{data.gate ? <Funnel size={11} aria-label="parks on a gate" /> : null}
					{data.compensation ? (
						<Undo2 size={11} aria-label="has a compensation topology" />
					) : null}
					{hasWarn ? (
						<AlertTriangle
							size={12}
							style={{
								color: hasError ? "var(--destructive)" : "var(--warning)",
							}}
							aria-label={issues.map((i) => i.message).join(" · ")}
						>
							<title>{issues.map((i) => i.message).join("\n")}</title>
						</AlertTriangle>
					) : null}
				</span>
			</div>
			<div className="mt-0.5 truncate text-muted-foreground">
				{data.topology ? `→ ${data.topology}` : "no topology"}
			</div>
			{data.isEntry ? (
				<div className="mt-0.5 text-[10px] uppercase tracking-wide text-sky-500">
					entry
				</div>
			) : null}
			<Handle
				id="out"
				type="source"
				position={Position.Right}
				isConnectable={data.editable}
				style={{ background: "var(--border)" }}
			/>
			<Handle
				id="loop-out"
				type="source"
				position={Position.Top}
				isConnectable={data.editable}
				style={{ background: SKY, left: "70%" }}
			/>
		</div>
	);
}

interface ExternalPinData {
	event: string;
	stage: string;
	kind: "entry" | "loop";
	[k: string]: unknown;
}
type ExternalFlowNode = Node<ExternalPinData, "external">;

/** An inbound external pin — a webhook (`entry`) or an external loop trigger (`loop`). Deliberately
 * NOT a stage box: it has no incoming side, only a source handle feeding the stage it triggers. */
function ExternalPin({ data }: NodeProps<ExternalFlowNode>) {
	const loop = data.kind === "loop";
	const color = loop ? "var(--warning)" : SKY;
	return (
		<div
			className="flex items-center gap-1 rounded-full border border-dashed bg-card px-2 py-1 text-[10px] text-muted-foreground shadow-sm"
			style={{ borderColor: color }}
		>
			{loop ? (
				<RotateCcw size={10} style={{ color }} />
			) : (
				<Webhook size={10} style={{ color }} />
			)}
			<span className="max-w-[130px] truncate font-mono">{data.event}</span>
			<Handle
				id="pin-out"
				type="source"
				position={Position.Right}
				isConnectable={false}
				style={{ background: color }}
			/>
		</div>
	);
}

const NODE_TYPES = { stage: StageCard, external: ExternalPin };

const EDGE_STYLE = {
	forward: { stroke: "var(--muted-foreground)", dash: undefined },
	loop: { stroke: "var(--warning)", dash: "5 4" },
	external: { stroke: SKY, dash: "4 4" },
} as const;

export interface StageGraphEditorProps {
	graph: StageGraphDoc;
	/** Ref lists (topology / funnel) for the inline ref-check validation. */
	refOptions?: RefOptions;
	/** The selected stage (ring highlight + inspector target). */
	selectedStage: string | null;
	onSelectStage: (id: string) => void;
	/** Edit mode: nodes connectable/deletable, palette drop enabled. Absent ⇒ read-only view. */
	editable?: boolean;
	/** Draw a signal edge A→B (right→left handles). */
	onDrawSignal?: (source: string, target: string) => void;
	/** Draw a loop edge into `target` on `when` (top→top handles). */
	onAddLoop?: (target: string, when: string) => void;
	/** Delete a signal edge A→B. */
	onDeleteSignal?: (source: string, target: string) => void;
	/** Delete a loop `{when, to}`. */
	onDeleteLoop?: (target: string, when: string) => void;
	/** Delete a stage (and its re-entry loops). */
	onRemoveStage?: (id: string) => void;
	/** Remove an inbound external entry `event` from `stage`. */
	onRemoveExternalEntry?: (stage: string, event: string) => void;
	/** Palette drop / drag of a topology onto the canvas ⇒ add a stage bound to it. */
	onAddStage?: (topology: string) => void;
	className?: string;
}

const PIN_DX = 190;

/**
 * Render a stage-graph as an editable left→right DAG. Layout, forward/loop edges, external entries,
 * and inline validation all come from the pure helpers; this component is the presentation +
 * gesture shell. Every gesture calls back so the page can apply the matching pure mutation and
 * round-trip through YAML — nodes never free-float into an invalid state.
 */
export function StageGraphEditor({
	graph: doc,
	refOptions,
	selectedStage,
	onSelectStage,
	editable = false,
	onDrawSignal,
	onAddLoop,
	onDeleteSignal,
	onDeleteLoop,
	onRemoveStage,
	onRemoveExternalEntry,
	onAddStage,
	className,
}: StageGraphEditorProps) {
	const projection = useMemo(() => stageGraphToGraph(doc), [doc]);
	const externals = useMemo(() => externalEntries(doc), [doc]);
	const issues = useMemo(
		() => issuesByStage(validateStageGraph(doc, refOptions)),
		[doc, refOptions],
	);

	const flow = useMemo(() => {
		const posById = new Map(projection.nodes.map((n) => [n.id, n.position]));

		const stageNodes = projection.nodes.map((n) => ({
			...n,
			data: {
				...n.data,
				selected: selectedStage === n.id,
				editable,
				issues: issues.get(n.id) ?? [],
			},
		})) as StageFlowNode[];

		// External inbound pins: each external `when` entry, and each loop whose trigger no stage
		// emits (externalLoops), rendered left of their target stage. Stacked so they don't overlap.
		const pinCount = new Map<string, number>();
		const pinNodes: ExternalFlowNode[] = [];
		const pinEdges: Edge[] = [];

		const addPin = (
			id: string,
			target: string,
			event: string,
			kind: "entry" | "loop",
		) => {
			const base = posById.get(target);
			if (!base) return;
			const idx = pinCount.get(target) ?? 0;
			pinCount.set(target, idx + 1);
			pinNodes.push({
				id,
				type: "external",
				position: { x: base.x - PIN_DX, y: base.y + idx * 42 },
				data: { event, stage: target, kind },
				connectable: false,
				deletable: editable,
			});
			pinEdges.push({
				id: `pinedge:${id}`,
				source: id,
				target,
				sourceHandle: "pin-out",
				targetHandle: kind === "loop" ? "loop-in" : "in",
				label: event,
				type: "smoothstep",
				animated: kind === "loop",
				deletable: false,
				focusable: false,
				labelStyle: {
					fill: EDGE_STYLE[kind === "loop" ? "loop" : "external"].stroke,
					fontSize: 10,
				},
				style: {
					stroke: EDGE_STYLE[kind === "loop" ? "loop" : "external"].stroke,
					strokeDasharray: EDGE_STYLE.external.dash,
				},
				markerEnd: {
					type: MarkerType.ArrowClosed,
					color: EDGE_STYLE[kind === "loop" ? "loop" : "external"].stroke,
				},
			});
		};

		for (const ext of externals)
			addPin(
				`ext-entry:${ext.stage}:${ext.event}`,
				ext.stage,
				ext.event,
				"entry",
			);
		for (const loop of projection.externalLoops)
			addPin(`ext-loop:${loop.to}:${loop.when}`, loop.to, loop.when, "loop");

		const graphEdges: Edge[] = projection.edges.map((e) => {
			const s = EDGE_STYLE[e.kind];
			const handles =
				e.kind === "loop"
					? { sourceHandle: "loop-out", targetHandle: "loop-in" }
					: { sourceHandle: "out", targetHandle: "in" };
			return {
				id: e.id,
				source: e.source,
				target: e.target,
				...handles,
				label: e.label,
				type: e.kind === "loop" ? "smoothstep" : "default",
				animated: e.kind === "loop",
				deletable: editable,
				data: { kind: e.kind, when: e.label },
				labelStyle: { fill: s.stroke, fontSize: 10 },
				style: { stroke: s.stroke, strokeDasharray: s.dash },
				markerEnd: { type: MarkerType.ArrowClosed, color: s.stroke },
			};
		});

		return {
			nodes: [...stageNodes, ...pinNodes] as Node[],
			edges: [...graphEdges, ...pinEdges],
		};
	}, [projection, externals, issues, selectedStage, editable]);

	const [nodes, setNodes, onNodesChange] = useNodesState<Node>(flow.nodes);
	const [edges, setEdges, onEdgesChange] = useEdgesState(flow.edges);
	useEffect(() => {
		setNodes(flow.nodes);
		setEdges(flow.edges);
	}, [flow, setNodes, setEdges]);

	const onNodeClick: ReactFlowProps["onNodeClick"] = (_e, node) => {
		if (node.type === "stage") onSelectStage(node.id);
	};

	// A connection from the top (loop) handle is a loop back-edge; anything else is a signal edge.
	// A new loop's trigger defaults to the source stage's success (or `<source>.reentry`); the exact
	// event is then editable in the inspector.
	const handleConnect: ReactFlowProps["onConnect"] = (conn) => {
		if (!conn.source || !conn.target) return;
		if (conn.sourceHandle === "loop-out" || conn.targetHandle === "loop-in") {
			const source = readStages(doc).find((s) => s.id === conn.source);
			const when = source?.success || `${conn.source}.reentry`;
			onAddLoop?.(conn.target, when);
		} else {
			onDrawSignal?.(conn.source, conn.target);
		}
	};

	const handleNodesDelete: ReactFlowProps["onNodesDelete"] = (deleted) => {
		for (const n of deleted) {
			if (n.id.startsWith("ext-entry:")) {
				const [, stage, event] = n.id.split(":");
				if (stage && event) onRemoveExternalEntry?.(stage, event);
			} else if (n.id.startsWith("ext-loop:")) {
				const [, to, when] = n.id.split(":");
				if (to && when) onDeleteLoop?.(to, when);
			} else {
				onRemoveStage?.(n.id);
			}
		}
	};

	const handleEdgesDelete: ReactFlowProps["onEdgesDelete"] = (deleted) => {
		for (const e of deleted) {
			const kind = (e.data as { kind?: string } | undefined)?.kind;
			if (kind === "loop") {
				const when = (e.data as { when?: string }).when;
				if (when) onDeleteLoop?.(e.target, when);
			} else if (kind === "forward") {
				onDeleteSignal?.(e.source, e.target);
			}
		}
	};

	const handleDrop = (e: React.DragEvent) => {
		e.preventDefault();
		const topology = e.dataTransfer.getData(STAGE_PALETTE_MIME);
		if (topology) onAddStage?.(topology);
	};

	const empty = projection.nodes.length === 0;

	return (
		<div
			className={className}
			style={{ width: "100%", height: "100%" }}
			onDragOver={
				editable
					? (e) => {
							e.preventDefault();
							e.dataTransfer.dropEffect = "copy";
						}
					: undefined
			}
			onDrop={editable ? handleDrop : undefined}
		>
			{empty ? (
				<div className="flex h-full flex-col items-center justify-center gap-1 text-sm text-muted-foreground">
					<span>No stages yet.</span>
					<span className="text-xs">
						{editable
							? "Drop a topology from the palette to add the first stage."
							: "Add one in the form or yaml view."}
					</span>
				</div>
			) : (
				<ReactFlow
					nodes={nodes}
					edges={edges}
					onNodesChange={onNodesChange}
					onEdgesChange={onEdgesChange}
					nodeTypes={NODE_TYPES}
					onNodeClick={onNodeClick}
					onConnect={editable ? handleConnect : undefined}
					onNodesDelete={editable ? handleNodesDelete : undefined}
					onEdgesDelete={editable ? handleEdgesDelete : undefined}
					fitView
					nodesDraggable={false}
					nodesConnectable={editable}
					edgesFocusable={editable}
					deleteKeyCode={editable ? ["Backspace", "Delete"] : null}
					proOptions={{ hideAttribution: true }}
				>
					<Background />
					<Controls showInteractive={false} />
					<MiniMap pannable zoomable />
				</ReactFlow>
			)}
			{!empty && (
				<div className="pointer-events-none absolute bottom-3 left-3 flex flex-col gap-1 rounded-md border bg-card/90 px-2 py-1.5 text-[10px] text-muted-foreground shadow-sm">
					<div className="flex items-center gap-1.5">
						<span
							className="h-px w-4"
							style={{ background: "var(--muted-foreground)" }}
						/>
						success → when (signal)
					</div>
					<div className="flex items-center gap-1.5">
						<Webhook size={10} style={{ color: SKY }} />
						external entry (webhook)
					</div>
					<div className="flex items-center gap-1.5">
						<span
							className="h-px w-4"
							style={{ background: "var(--warning)" }}
						/>
						loop (defect cycle)
					</div>
				</div>
			)}
		</div>
	);
}

export type { ExternalEntry, StageLoop };
