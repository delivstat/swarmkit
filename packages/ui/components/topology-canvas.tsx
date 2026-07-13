"use client";

// The topology canvas (design/details/topology-canvas.md): a topology rendered as an interactive
// node-and-edge graph. This slice is view-only — agents as cards, delegation as edges, auto-laid
// out; pan/zoom/minimap. Edit + examine-run modes build on this same component in later slices.

import "@xyflow/react/dist/style.css";
import type { AgentNodeData } from "@/lib/topology-graph";
import { topologyToGraph } from "@/lib/topology-graph";
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
} from "@xyflow/react";
import { Crown, Shield, User } from "lucide-react";
import { useMemo } from "react";

const WORKER_STYLE = { color: "var(--success)", icon: User };
const ROLE_STYLE: Record<string, { color: string; icon: typeof Crown }> = {
	root: { color: "var(--accent)", icon: Crown },
	leader: { color: "var(--warning)", icon: Shield },
	worker: WORKER_STYLE,
};

type AgentNode = Node<AgentNodeData, "agent">;

/** A custom node = an agent card (id, role icon, archetype, skill count). */
function AgentCard({ data, selected }: NodeProps<AgentNode>) {
	const style = ROLE_STYLE[data.role] ?? WORKER_STYLE;
	const Icon = style.icon;
	return (
		<div
			className="rounded-lg px-3 py-2 text-xs shadow-sm"
			style={{
				background: "var(--bg-sidebar)",
				border: `2px solid ${selected ? style.color : "var(--border)"}`,
				minWidth: 150,
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
			<div className="mt-0.5" style={{ color: "var(--fg-muted)" }}>
				{data.role}
				{data.archetype ? ` · ${data.archetype}` : ""}
			</div>
			<div style={{ color: "var(--fg-muted)" }}>
				{data.skillCount} skill{data.skillCount === 1 ? "" : "s"}
			</div>
			<Handle
				type="source"
				position={Position.Bottom}
				style={{ background: "var(--border)" }}
			/>
		</div>
	);
}

const NODE_TYPES = { agent: AgentCard };

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
	className?: string;
}

/**
 * Render a topology as an interactive graph. The layout comes from the pure `topologyToGraph`
 * helper; this component is the presentation + interaction shell. In `editable` mode, drawing an
 * edge or deleting a node calls back so the composer can round-trip the change through YAML.
 */
export function TopologyCanvas({
	root,
	onSelect,
	editable = false,
	onConnect,
	onDeleteNode,
	className,
}: TopologyCanvasProps) {
	const { nodes, edges } = useMemo(() => {
		const g = topologyToGraph(root);
		return {
			nodes: g.nodes as AgentNode[],
			edges: g.edges as Edge[],
		};
	}, [root]);

	const onNodeClick: ReactFlowProps["onNodeClick"] = (_e, node) =>
		onSelect?.(node.id);

	const handleConnect: ReactFlowProps["onConnect"] = (conn) => {
		if (conn.source && conn.target) onConnect?.(conn.source, conn.target);
	};

	const handleNodesDelete: ReactFlowProps["onNodesDelete"] = (deleted) => {
		for (const n of deleted) onDeleteNode?.(n.id);
	};

	return (
		<div className={className} style={{ width: "100%", height: "100%" }}>
			<ReactFlow
				nodes={nodes}
				edges={edges}
				nodeTypes={NODE_TYPES}
				onNodeClick={onNodeClick}
				onConnect={handleConnect}
				onNodesDelete={handleNodesDelete}
				fitView
				nodesDraggable={false}
				nodesConnectable={editable}
				deleteKeyCode={editable ? ["Backspace", "Delete"] : null}
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
