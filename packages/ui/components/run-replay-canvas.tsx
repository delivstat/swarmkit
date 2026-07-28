"use client";

// The pipeline-run REPLAY canvas (design/details/bundled-pipeline-orchestrator.md §4): a StageGraph
// rendered as its left→right DAG, each stage coloured by its status *within this run* (passed /
// active / parked / rejected / failed / pending). Read-only — nodes are clickable to open the node
// inspector, edges are never editable, nodes never draggable. It reuses the pure `stageGraphToGraph`
// projection (the same layout the authoring canvas uses) and folds the saga's run state onto it via
// `stageStatus`, so the run "plays back" over the pipeline definition.

import "@xyflow/react/dist/style.css";
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
	useEdgesState,
	useNodesState,
} from "@xyflow/react";
import { Boxes, Funnel } from "lucide-react";
import { useEffect, useMemo } from "react";

import {
	RUN_STAGE_META,
	type RunStageStatus,
	stageStatus,
} from "@/lib/run-status";
import { type StageGraphDoc, stageGraphToGraph } from "@/lib/stage-graph";
import type { SagaDetail } from "@/lib/types";

interface RunNodeData extends Record<string, unknown> {
	id: string;
	topology: string | null;
	gate: string | null;
	runStatus: RunStageStatus;
	attempts: number;
	selected: boolean;
}
type RunFlowNode = Node<RunNodeData, "runStage">;

/** A custom node = one pipeline stage, ringed + dotted by its run status. */
function RunStageCard({ data }: NodeProps<RunFlowNode>) {
	const meta = RUN_STAGE_META[data.runStatus];
	return (
		<div
			className="min-w-[168px] rounded-lg border-2 bg-card px-3 py-2 text-xs text-card-foreground shadow-sm"
			style={{
				borderColor: meta.color,
				boxShadow: data.selected ? `0 0 0 3px ${meta.color}44` : undefined,
			}}
		>
			<Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
			<div className="flex items-center justify-between gap-2">
				<span className="font-mono font-medium">{data.id}</span>
				<span
					className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
					style={{ backgroundColor: meta.color }}
					title={meta.label}
				/>
			</div>
			<div className="mt-1 flex items-center gap-1 text-muted-foreground">
				<Boxes className="h-3 w-3" />
				<span className="truncate">{data.topology ?? "—"}</span>
			</div>
			<div className="mt-1 flex items-center gap-2">
				<span style={{ color: meta.color }}>{meta.label}</span>
				{data.gate ? (
					<span
						className="flex items-center gap-0.5 text-muted-foreground"
						title={`gate: ${data.gate}`}
					>
						<Funnel className="h-3 w-3" />
						gate
					</span>
				) : null}
				{data.attempts > 1 ? (
					<span className="text-muted-foreground" title="attempts">
						×{data.attempts}
					</span>
				) : null}
			</div>
			<Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
		</div>
	);
}

const NODE_TYPES = { runStage: RunStageCard };

export interface RunReplayCanvasProps {
	doc: StageGraphDoc;
	saga: SagaDetail;
	selectedStage: string | null;
	onSelectStage: (stageId: string) => void;
}

/** The read-only replay canvas: the graph's DAG, coloured by this run's per-stage status. */
export function RunReplayCanvas({
	doc,
	saga,
	selectedStage,
	onSelectStage,
}: RunReplayCanvasProps) {
	const projection = useMemo(() => stageGraphToGraph(doc), [doc]);

	const initialNodes = useMemo<RunFlowNode[]>(
		() =>
			projection.nodes.map((n) => ({
				id: n.id,
				type: "runStage" as const,
				position: n.position,
				data: {
					id: n.data.id,
					topology: n.data.topology,
					gate: n.data.gate,
					runStatus: stageStatus(saga, n.data.id),
					attempts: saga.attempts[n.data.id] ?? 0,
					selected: n.data.id === selectedStage,
				},
			})),
		[projection, saga, selectedStage],
	);

	const initialEdges = useMemo<Edge[]>(
		() =>
			projection.edges.map((e) => ({
				id: e.id,
				source: e.source,
				target: e.target,
				label: e.label,
				animated: e.kind === "forward" && saga.current_stage === e.target,
				style:
					e.kind === "loop"
						? { stroke: "#f59e0b", strokeDasharray: "4 3" }
						: undefined,
				markerEnd: { type: MarkerType.ArrowClosed },
			})),
		[projection, saga],
	);

	const [nodes, setNodes, onNodesChange] =
		useNodesState<RunFlowNode>(initialNodes);
	const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);

	// Re-project when the run or selection changes (a poll refresh replays the new state).
	useEffect(() => setNodes(initialNodes), [initialNodes, setNodes]);
	useEffect(() => setEdges(initialEdges), [initialEdges, setEdges]);

	if (projection.nodes.length === 0) {
		return (
			<div className="flex h-full items-center justify-center text-sm text-muted-foreground">
				This run's pipeline definition ({saga.graph}) could not be loaded to
				replay.
			</div>
		);
	}

	return (
		<ReactFlow
			nodes={nodes}
			edges={edges}
			onNodesChange={onNodesChange}
			onEdgesChange={onEdgesChange}
			nodeTypes={NODE_TYPES}
			onNodeClick={(_, node) => onSelectStage(node.id)}
			nodesDraggable={false}
			nodesConnectable={false}
			elementsSelectable
			fitView
			proOptions={{ hideAttribution: true }}
		>
			<Background />
			<Controls showInteractive={false} />
			<MiniMap pannable zoomable />
		</ReactFlow>
	);
}
