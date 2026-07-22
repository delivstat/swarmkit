"use client";

// The pipeline canvas (design/details/pipeline-controller.md): a StageGraph rendered as its left→right
// DAG — stages as cards, wired by SIGNAL (a stage's `success` feeding the next stage's `when`), with
// the cross-stage `loops` (the defect cycle) drawn as dashed edges back into earlier stages.
//
// This is a READ-ONLY visualization, not an editor: unlike the funnel's fixed pipeline, a stage graph
// is a genuine DAG whose wiring is derived from the signals, so there is no meaningful "draw an edge"
// gesture — you change the wiring by editing a stage's `success`/`when` in the form or yaml view.
// Nodes are clickable (to select a stage for the read-only detail panel the page owns); edges are
// never editable and nodes are never draggable/connectable/deletable.

import "@xyflow/react/dist/style.css";
import { contractLabel } from "@/lib/contract";
import type { StageGraphDoc, StageNodeData } from "@/lib/stage-graph";
import { contendedContracts, stageGraphToGraph } from "@/lib/stage-graph";
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
import { Boxes, Funnel, Link2, Lock, RotateCcw, Undo2 } from "lucide-react";
import { useEffect, useMemo } from "react";

interface CanvasStageData extends StageNodeData {
	selected?: boolean;
	/** Contract id → parties, for labelling contended locks by the apps they bind (if fetched). */
	contractParties?: Record<string, string[]>;
}
type StageFlowNode = Node<CanvasStageData, "stage">;

/** The tooltip for the contended-lock badge: each shared contract labelled by its parties (if known),
 * one per line — so hovering a contended stage says exactly which contracts it shares. */
function contendedTitle(
	contended: string[],
	parties: Record<string, string[]> | undefined,
): string {
	return contended.map((c) => contractLabel(c, parties?.[c] ?? [])).join("\n");
}

/** A custom node = one pipeline stage. Entry stages ring in sky; the selected stage rings in its
 * accent. Small markers flag a gate, held locks, a contended (shared) contract, and a compensation
 * topology. */
function StageCard({ data }: NodeProps<StageFlowNode>) {
	const entryColor = "#0ea5e9";
	const accent = data.selected
		? entryColor
		: data.isEntry
			? entryColor
			: "var(--border)";
	const contended = data.contendedLocks ?? [];
	return (
		<div
			className="min-w-[170px] rounded-lg border-2 bg-card px-3 py-2 text-xs text-card-foreground shadow-sm"
			style={{ borderColor: accent }}
		>
			<Handle
				id="in"
				type="target"
				position={Position.Left}
				style={{ background: "var(--border)" }}
			/>
			<Handle
				id="loop-in"
				type="target"
				position={Position.Top}
				style={{ background: "var(--border)" }}
			/>
			<div className="flex items-center gap-1.5 font-medium">
				<Boxes size={14} style={{ color: entryColor }} />
				<span>{data.id}</span>
				<span className="ml-auto flex items-center gap-1 text-muted-foreground">
					{data.locks.length > 0 ? (
						<Lock size={11} aria-label={`holds ${data.locks.length} lock(s)`} />
					) : null}
					{contended.length > 0 ? (
						<Link2
							size={12}
							style={{ color: "var(--warning)" }}
							aria-label={`shares ${contended.length} contract(s) with another stage`}
						>
							<title>{contendedTitle(contended, data.contractParties)}</title>
						</Link2>
					) : null}
					{data.gate ? <Funnel size={11} aria-label="parks on a gate" /> : null}
					{data.compensation ? (
						<Undo2 size={11} aria-label="has a compensation topology" />
					) : null}
				</span>
			</div>
			<div className="mt-0.5 truncate text-muted-foreground">
				{data.topology ? `→ ${data.topology}` : "no topology"}
			</div>
			{contended.length > 0 ? (
				<div
					className="mt-0.5 flex items-center gap-1 truncate text-[10px]"
					style={{ color: "var(--warning)" }}
					title={contendedTitle(contended, data.contractParties)}
				>
					<Link2 size={10} />
					<span className="truncate">shared: {contended.join(", ")}</span>
				</div>
			) : null}
			{data.isEntry ? (
				<div className="mt-0.5 text-[10px] uppercase tracking-wide text-sky-500">
					entry
				</div>
			) : null}
			<Handle
				id="out"
				type="source"
				position={Position.Right}
				style={{ background: "var(--border)" }}
			/>
			<Handle
				id="loop-out"
				type="source"
				position={Position.Top}
				style={{ background: "var(--border)" }}
			/>
		</div>
	);
}

const NODE_TYPES = { stage: StageCard };

const EDGE_STYLE = {
	forward: { stroke: "var(--muted-foreground)", dash: undefined },
	loop: { stroke: "var(--warning)", dash: "5 4" },
} as const;

export interface StageGraphCanvasProps {
	graph: StageGraphDoc;
	/** The stage whose read-only detail panel is open, for the ring highlight. */
	selectedStage: string | null;
	onSelectStage: (id: string) => void;
	/** Contract id → parties, to label contended (shared) locks by the apps they bind. Optional — the
	 * contention highlight works from lock ids alone; parties only enrich the tooltip when fetched. */
	contractParties?: Record<string, string[]>;
	className?: string;
}

/**
 * Render a stage-graph as its left→right DAG. Layout + edges come from the pure `stageGraphToGraph`
 * helper; this component is the presentation shell. Read-only: nodes are never draggable,
 * connectable, or deletable — the wiring is derived from the stages' signals, not drawn here.
 */
export function StageGraphCanvas({
	graph: doc,
	selectedStage,
	onSelectStage,
	contractParties,
	className,
}: StageGraphCanvasProps) {
	const projection = useMemo(() => stageGraphToGraph(doc), [doc]);

	const flow = useMemo(() => {
		const nodes = projection.nodes.map((n) => ({
			...n,
			data: { ...n.data, selected: selectedStage === n.id, contractParties },
		})) as StageFlowNode[];
		const edges: Edge[] = projection.edges.map((e) => {
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
				labelStyle: { fill: s.stroke, fontSize: 10 },
				style: { stroke: s.stroke, strokeDasharray: s.dash },
				markerEnd: { type: MarkerType.ArrowClosed, color: s.stroke },
			};
		});
		return { nodes, edges };
	}, [projection, selectedStage, contractParties]);

	const [nodes, setNodes, onNodesChange] = useNodesState<StageFlowNode>(
		flow.nodes,
	);
	const [edges, setEdges, onEdgesChange] = useEdgesState(flow.edges);
	useEffect(() => {
		setNodes(flow.nodes);
		setEdges(flow.edges);
	}, [flow, setNodes, setEdges]);

	const onNodeClick: ReactFlowProps["onNodeClick"] = (_e, node) =>
		onSelectStage(node.id);

	const contendedCount = useMemo(() => contendedContracts(doc).length, [doc]);
	const empty = projection.nodes.length === 0;

	return (
		<div className={className} style={{ width: "100%", height: "100%" }}>
			{empty ? (
				<div className="flex h-full items-center justify-center text-sm text-muted-foreground">
					No stages yet. Add one in the form or yaml view.
				</div>
			) : (
				<ReactFlow
					nodes={nodes}
					edges={edges}
					onNodesChange={onNodesChange}
					onEdgesChange={onEdgesChange}
					nodeTypes={NODE_TYPES}
					onNodeClick={onNodeClick}
					fitView
					nodesDraggable={false}
					nodesConnectable={false}
					edgesFocusable={false}
					deleteKeyCode={null}
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
						success → when (forward)
					</div>
					<div className="flex items-center gap-1.5">
						<span
							className="h-px w-4"
							style={{ background: "var(--warning)" }}
						/>
						loop (defect cycle)
					</div>
					{contendedCount > 0 && (
						<div className="flex items-center gap-1.5">
							<Link2 size={10} style={{ color: "var(--warning)" }} />
							{contendedCount} shared contract
							{contendedCount === 1 ? "" : "s"}
						</div>
					)}
					{projection.externalLoops.length > 0 && (
						<div className="flex items-center gap-1.5">
							<RotateCcw size={10} />
							{projection.externalLoops.length} external loop
							{projection.externalLoops.length === 1 ? "" : "s"}
						</div>
					)}
				</div>
			)}
		</div>
	);
}
