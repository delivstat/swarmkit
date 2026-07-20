"use client";

// The funnel pipeline canvas (design/details/gate-funnel.md): a Funnel rendered as its FIXED
// left-to-right gate pipeline — draft → validate → judge → review → approve → done — with the
// bounded retry loop back to draft and the retry-exhaustion escalation to the human `approve` gate.
//
// This is a STRUCTURED PIPELINE EDITOR, not free BPMN: the node rack and edges are compiler-owned
// and are NOT user-drawable or rewireable (that would break the "no path to done except through a
// human" invariant). Users CONFIGURE each layer (click → per-layer panel, owned by the page) and
// TOGGLE the optional layers on/off (validate/judge/review); `approve` is always present. Edges are
// rendered, never editable.

import "@xyflow/react/dist/style.css";
import { Switch } from "@/components/ui/switch";
import type {
	FunnelDoc,
	FunnelLayerKey,
	FunnelNodeData,
	OptionalLayerKey,
} from "@/lib/funnel-graph";
import { funnelToGraph } from "@/lib/funnel-graph";
import { cn } from "@/lib/utils";
import {
	Background,
	Controls,
	type Edge,
	Handle,
	MarkerType,
	type Node,
	type NodeProps,
	Position,
	ReactFlow,
	type ReactFlowProps,
	useEdgesState,
	useNodesState,
} from "@xyflow/react";
import {
	CircleCheckBig,
	FileCheck,
	Gavel,
	PenLine,
	Scale,
	UserCheck,
} from "lucide-react";
import { useEffect, useMemo } from "react";

/** Per-layer icon + accent. `approve` gets sky (the human gate); automated layers a neutral accent;
 * anchors are muted. Colors resolve against the design tokens (accent is sky, not blue). */
const LAYER_STYLE: Record<
	FunnelLayerKey,
	{ color: string; icon: typeof PenLine }
> = {
	draft: { color: "var(--muted-foreground)", icon: PenLine },
	validate: { color: "var(--success)", icon: FileCheck },
	judge: { color: "var(--warning)", icon: Scale },
	review: { color: "var(--warning)", icon: Gavel },
	approve: { color: "#0ea5e9", icon: UserCheck },
	done: { color: "var(--success)", icon: CircleCheckBig },
};

/** Node data carries the presentational + interaction fields the page injects per render. */
interface CanvasLayerData extends FunnelNodeData {
	selected?: boolean;
	editable?: boolean;
	summary?: string;
	onToggle?: (key: OptionalLayerKey, on: boolean) => void;
}
type LayerNode = Node<CanvasLayerData, "layer">;

/** A custom node = one funnel layer. Inactive (toggled-off) layers dim; the selected layer rings in
 * its accent; optional layers show a toggle in edit mode; `approve` shows a required marker. */
function LayerCard({ data }: NodeProps<LayerNode>) {
	const style = LAYER_STYLE[data.key];
	const Icon = style.icon;
	const inactive = !data.present;
	return (
		<div
			className={cn(
				"min-w-[150px] rounded-lg border-2 bg-card px-3 py-2 text-xs text-card-foreground shadow-sm transition-opacity",
			)}
			style={{
				borderColor: data.selected ? style.color : "var(--border)",
				opacity: inactive ? 0.4 : 1,
			}}
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
				position={Position.Bottom}
				style={{ background: "var(--border)" }}
			/>
			<Handle
				id="top-in"
				type="target"
				position={Position.Top}
				style={{ background: "var(--border)" }}
			/>
			<div className="flex items-center gap-1.5 font-medium">
				<Icon size={14} style={{ color: style.color }} />
				<span>{data.label}</span>
				{data.required ? (
					<span className="ml-auto text-[10px] uppercase text-muted-foreground">
						required
					</span>
				) : data.optional && data.editable ? (
					<span className="ml-auto">
						<Switch
							checked={data.present}
							onCheckedChange={(on) =>
								data.onToggle?.(data.key as OptionalLayerKey, on)
							}
							// Let the toggle handle the click without also selecting the node.
							onClick={(e) => e.stopPropagation()}
						/>
					</span>
				) : null}
			</div>
			<div className="mt-0.5 text-muted-foreground">
				{inactive
					? "inactive"
					: data.present && !data.configured && !data.required
						? "needs config"
						: (data.summary ?? "")}
			</div>
			<Handle
				id="out"
				type="source"
				position={Position.Right}
				style={{ background: "var(--border)" }}
			/>
			<Handle
				id="loop-out"
				type="source"
				position={Position.Bottom}
				style={{ background: "var(--border)" }}
			/>
			<Handle
				id="top-out"
				type="source"
				position={Position.Top}
				style={{ background: "var(--border)" }}
			/>
		</div>
	);
}

const NODE_TYPES = { layer: LayerCard };

/** A short per-layer config summary for the node card, read straight off the funnel document. */
function layerSummary(funnel: FunnelDoc, key: FunnelLayerKey): string {
	if (key === "draft") return "agent output";
	if (key === "done") return "released";
	const block = (funnel?.[key] ?? {}) as Record<string, unknown>;
	switch (key) {
		case "validate": {
			const schema = typeof block.schema === "string" ? block.schema : null;
			const ac = block.autocorrect === false ? "no autocorrect" : "autocorrect";
			return schema ? `${schema.split("/").pop()} · ${ac}` : ac;
		}
		case "judge": {
			const skill = typeof block.skill === "string" ? block.skill : "—";
			const t = typeof block.threshold === "number" ? block.threshold : 0.8;
			return `${skill} · ≥ ${t}`;
		}
		case "review": {
			const arch = typeof block.archetype === "string" ? block.archetype : "—";
			const rb =
				typeof block.route_back_at === "string" ? block.route_back_at : "high";
			return `${arch} · ↩ ${rb}`;
		}
		case "approve": {
			const rules = Array.isArray(block.rules) ? block.rules.length : 0;
			return `${rules} rule${rules === 1 ? "" : "s"}`;
		}
		default:
			return "";
	}
}

const EDGE_STYLE = {
	forward: { stroke: "var(--muted-foreground)", dash: undefined },
	retry: { stroke: "var(--warning)", dash: "5 4" },
	escalate: { stroke: "var(--destructive)", dash: "5 4" },
} as const;

export interface FunnelCanvasProps {
	funnel: FunnelDoc;
	/** The layer whose config panel is open (null ⇒ funnel details), for the ring highlight. */
	selectedLayer: FunnelLayerKey | null;
	onSelectLayer: (key: FunnelLayerKey) => void;
	/** Edit mode: optional-layer toggles show on the cards. Absent ⇒ read-only. */
	editable?: boolean;
	/** Toggle an optional layer on/off. */
	onToggleLayer?: (key: OptionalLayerKey, on: boolean) => void;
	className?: string;
}

/**
 * Render a funnel as its fixed gate pipeline. Layout + edges come from the pure `funnelToGraph`
 * helper; this component is the presentation + interaction shell. Nodes are never draggable,
 * connectable, or deletable — a funnel configures layers, it does not rewire the graph.
 */
export function FunnelCanvas({
	funnel,
	selectedLayer,
	onSelectLayer,
	editable = false,
	onToggleLayer,
	className,
}: FunnelCanvasProps) {
	const graph = useMemo(() => {
		const g = funnelToGraph(funnel);
		const nodes = g.nodes.map((n) => ({
			...n,
			data: {
				...n.data,
				selected: selectedLayer === n.id,
				editable,
				summary: layerSummary(funnel, n.id),
				onToggle: onToggleLayer,
			},
		})) as LayerNode[];
		const edges: Edge[] = g.edges.map((e) => {
			const s = EDGE_STYLE[e.kind];
			const handles =
				e.kind === "retry"
					? { sourceHandle: "loop-out", targetHandle: "loop-in" }
					: e.kind === "escalate"
						? { sourceHandle: "top-out", targetHandle: "top-in" }
						: { sourceHandle: "out", targetHandle: "in" };
			return {
				id: e.id,
				source: e.source,
				target: e.target,
				...handles,
				label: e.label,
				type: e.kind === "forward" ? "default" : "smoothstep",
				animated: e.kind !== "forward",
				labelStyle: { fill: s.stroke, fontSize: 10 },
				style: { stroke: s.stroke, strokeDasharray: s.dash },
				markerEnd: { type: MarkerType.ArrowClosed, color: s.stroke },
			};
		});
		return { nodes, edges };
	}, [funnel, selectedLayer, editable, onToggleLayer]);

	const [nodes, setNodes, onNodesChange] = useNodesState<LayerNode>(
		graph.nodes,
	);
	const [edges, setEdges, onEdgesChange] = useEdgesState(graph.edges);
	useEffect(() => {
		setNodes(graph.nodes);
		setEdges(graph.edges);
	}, [graph, setNodes, setEdges]);

	const onNodeClick: ReactFlowProps["onNodeClick"] = (_e, node) =>
		onSelectLayer(node.id as FunnelLayerKey);

	return (
		<div className={className} style={{ width: "100%", height: "100%" }}>
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
			</ReactFlow>
			<div className="pointer-events-none absolute bottom-3 left-3 flex flex-col gap-1 rounded-md border bg-card/90 px-2 py-1.5 text-[10px] text-muted-foreground shadow-sm">
				<div className="flex items-center gap-1.5">
					<span className="h-px w-4" style={{ background: "var(--warning)" }} />
					retry → draft (bounded)
				</div>
				<div className="flex items-center gap-1.5">
					<span
						className="h-px w-4"
						style={{ background: "var(--destructive)" }}
					/>
					escalate → human gate
				</div>
			</div>
		</div>
	);
}
