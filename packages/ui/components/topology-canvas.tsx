"use client";

// The topology canvas (design/details/topology-canvas.md): a topology rendered as an interactive
// node-and-edge graph. This slice is view-only — agents as cards, delegation as edges, auto-laid
// out; pan/zoom/minimap. Edit + examine-run modes build on this same component in later slices.

import "@xyflow/react/dist/style.css";
import { formatTokens, formatUsd } from "@/lib/format";
import type { AgentNodeData } from "@/lib/topology-graph";
import { topologyToGraph } from "@/lib/topology-graph";
import type { NodeRun } from "@/lib/topology-run";
import type { ResolvedAgent } from "@/lib/types";
import {
	Background,
	Controls,
	type Edge,
	Handle,
	MiniMap,
	type Node,
	type NodeProps,
	Position,
	ReactFlow,
	type ReactFlowProps,
	useEdgesState,
	useNodesState,
} from "@xyflow/react";
import { Crown, Shield, User } from "lucide-react";
import { useEffect, useMemo } from "react";

// Dynamic per-node colors (role accent + run-status ring) stay inline: a node card colors its icon,
// border and cost line independently, so a single currentColor can't drive all three. Values resolve
// against the design tokens; `root` uses sky (the old `--accent` blue is now a neutral token).
const WORKER_STYLE = { color: "var(--success)", icon: User };
const ROLE_STYLE: Record<string, { color: string; icon: typeof Crown }> = {
	root: { color: "#0ea5e9", icon: Crown },
	leader: { color: "var(--warning)", icon: Shield },
	worker: WORKER_STYLE,
};

/** Node data carries the run overlay in examine mode: `run` if the agent fired, `examine` marks the
 * overlay active (so a node with no `run` reads as "did not fire" — dimmed). */
interface CanvasNodeData extends AgentNodeData {
	run?: NodeRun;
	examine?: boolean;
}
type AgentNode = Node<CanvasNodeData, "agent">;

/** A custom node = an agent card. In examine mode a status ring + cost/duration overlay the card,
 * and a node that did not fire is dimmed. */
function AgentCard({ data, selected }: NodeProps<AgentNode>) {
	const style = ROLE_STYLE[data.role] ?? WORKER_STYLE;
	const Icon = style.icon;
	const run = data.run;
	const notFired = data.examine && !run;
	const ring = run
		? run.status === "error"
			? "var(--destructive)"
			: "var(--success)"
		: undefined;
	return (
		<div
			className="min-w-[150px] rounded-lg border-2 bg-card px-3 py-2 text-xs text-card-foreground shadow-sm"
			style={{
				borderColor: selected ? style.color : (ring ?? "var(--border)"),
				opacity: notFired ? 0.45 : 1,
			}}
		>
			<Handle
				type="target"
				position={Position.Top}
				style={{ background: "var(--border)" }}
			/>
			<div className="flex items-center gap-1.5 font-medium">
				<Icon size={14} style={{ color: style.color }} />
				<span>{data.id}</span>
			</div>
			<div className="mt-0.5 text-muted-foreground">
				{data.role}
				{data.archetype ? ` · ${data.archetype}` : ""}
			</div>
			{run ? (
				<div style={{ color: ring }}>
					{formatUsd(run.costUsd)} · {Math.round(run.durationMs)}ms ·{" "}
					{formatTokens(run.tokens)} tok
				</div>
			) : (
				<div className="text-muted-foreground">
					{data.examine
						? "did not fire"
						: `${data.skillCount} skill${data.skillCount === 1 ? "" : "s"}`}
				</div>
			)}
			<Handle
				type="source"
				position={Position.Bottom}
				style={{ background: "var(--border)" }}
			/>
		</div>
	);
}

const NODE_TYPES = { agent: AgentCard };

/** dataTransfer MIME the palette (archetype/skill/worker chips) and the canvas drop handler share. */
export const PALETTE_MIME = "application/swarmkit-node";

/** A palette drag payload — `kind: "archetype"` carries an archetype id in `value`, `"skill"` a skill
 * id, `"worker"` is a blank agent. Set on dragstart, read on drop. */
export interface PaletteDrag {
	kind: "archetype" | "skill" | "worker";
	value?: string;
}

export interface TopologyCanvasProps {
	root: ResolvedAgent | null | undefined;
	/** Called when a node (agent) is clicked — the id, for a detail panel. */
	onSelect?: (agentId: string) => void;
	/** Edit mode: allow drawing delegation edges + deleting nodes. Absent ⇒ read-only. */
	editable?: boolean;
	/** Draw a delegation edge: `source` delegates to `target` (target re-parents under source). */
	onConnect?: (source: string, target: string) => void;
	/** Delete an agent (and its subtree). */
	onDeleteNode?: (agentId: string) => void;
	/** Palette drop: add a child agent. `targetId` is the node dropped onto (else null → the caller
	 * picks a parent, e.g. the selected node or root); `archetypeId` instantiates that archetype. */
	onAddChild?: (targetId: string | null, archetypeId?: string) => void;
	/** Palette drop of a skill onto an agent node. */
	onAddSkill?: (agentId: string, skillId: string) => void;
	/** Examine mode: a run overlay keyed by agent id. Present ⇒ nodes show cost/duration/status and
	 * non-fired agents dim. Read-only regardless of `editable`. */
	overlay?: Record<string, NodeRun>;
	className?: string;
}

/**
 * Render a topology as an interactive graph. The layout comes from the pure `topologyToGraph`
 * helper; this component is the presentation + interaction shell. In `editable` mode, drawing an
 * edge or deleting a node calls back so the composer can round-trip the change through YAML. With an
 * `overlay`, it becomes an examine view — a run mapped onto the same graph.
 */
export function TopologyCanvas({
	root,
	onSelect,
	editable = false,
	onConnect,
	onDeleteNode,
	onAddChild,
	onAddSkill,
	overlay,
	className,
}: TopologyCanvasProps) {
	const examine = overlay !== undefined;
	const graph = useMemo(() => {
		const g = topologyToGraph(root);
		const nodes = g.nodes.map((n) => ({
			...n,
			data: examine
				? { ...n.data, run: overlay?.[n.id], examine: true }
				: n.data,
		})) as AgentNode[];
		return { nodes, edges: g.edges as Edge[] };
	}, [root, overlay, examine]);

	// React Flow needs a change handler to own selection/interaction state — without it, controlled
	// `nodes` can't be selected, so the Delete key has nothing to remove. `useNodesState` provides it;
	// we re-seed whenever the (draft) topology changes, which also re-runs the tidy-tree layout.
	const [nodes, setNodes, onNodesChange] = useNodesState<AgentNode>(
		graph.nodes,
	);
	const [edges, setEdges, onEdgesChange] = useEdgesState(graph.edges);
	useEffect(() => {
		setNodes(graph.nodes);
		setEdges(graph.edges);
	}, [graph, setNodes, setEdges]);

	const onNodeClick: ReactFlowProps["onNodeClick"] = (_e, node) =>
		onSelect?.(node.id);

	const handleConnect: ReactFlowProps["onConnect"] = (conn) => {
		if (conn.source && conn.target) onConnect?.(conn.source, conn.target);
	};

	const handleNodesDelete: ReactFlowProps["onNodesDelete"] = (deleted) => {
		for (const n of deleted) onDeleteNode?.(n.id);
	};

	const dnd = editable && !examine && (onAddChild || onAddSkill);

	// Palette drop: find the agent node under the cursor (if any) via the DOM — React Flow tags each
	// node wrapper with `data-id` — so we avoid flow-coordinate math. A skill must land on a node; an
	// archetype/worker can land anywhere (null target → the composer parents it under the selection).
	const handleDrop = (e: React.DragEvent) => {
		e.preventDefault();
		const raw = e.dataTransfer.getData(PALETTE_MIME);
		if (!raw) return;
		let payload: { kind: string; value?: string };
		try {
			payload = JSON.parse(raw);
		} catch {
			return;
		}
		const el = document.elementFromPoint(
			e.clientX,
			e.clientY,
		) as Element | null;
		const targetId =
			el?.closest(".react-flow__node")?.getAttribute("data-id") ?? null;
		if (payload.kind === "skill") {
			if (targetId && payload.value) onAddSkill?.(targetId, payload.value);
		} else {
			onAddChild?.(
				targetId,
				payload.kind === "archetype" ? payload.value : undefined,
			);
		}
	};

	return (
		<div
			className={className}
			style={{ width: "100%", height: "100%" }}
			onDragOver={
				dnd
					? (e) => {
							e.preventDefault();
							e.dataTransfer.dropEffect = "copy";
						}
					: undefined
			}
			onDrop={dnd ? handleDrop : undefined}
		>
			<ReactFlow
				nodes={nodes}
				edges={edges}
				onNodesChange={onNodesChange}
				onEdgesChange={onEdgesChange}
				nodeTypes={NODE_TYPES}
				onNodeClick={onNodeClick}
				onConnect={handleConnect}
				onNodesDelete={handleNodesDelete}
				fitView
				nodesDraggable={false}
				nodesConnectable={editable && !examine}
				deleteKeyCode={editable && !examine ? ["Backspace", "Delete"] : null}
				edgesFocusable={false}
				proOptions={{ hideAttribution: true }}
			>
				<Background />
				<Controls showInteractive={false} />
				<MiniMap pannable zoomable />
			</ReactFlow>
		</div>
	);
}
