"use client";

// The fleet run graph (design/details/fleet-run-graph.md): a completed run rendered as a
// read-only agent-and-delegation graph, built from the federated trace ("what actually ran").
// Read-only — the fleet UI is an observability surface; no editing.

import "@xyflow/react/dist/style.css";
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
	useEdgesState,
	useNodesState,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo } from "react";

import { api } from "@/lib/api";
import { type RunNodeData, traceToGraph } from "@/lib/trace-graph";
import type { RunTraceEnvelope } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

type AgentNode = Node<RunNodeData, "agent">;

function usd(n: number): string {
	return n > 0 ? `$${n.toFixed(n < 0.01 ? 4 : 2)}` : "$0";
}

/** One agent's card: id, role, and its run stats. Border is red if any of its steps errored. */
function AgentCard({ data }: NodeProps<AgentNode>) {
	const errored = data.status === "error";
	return (
		<div
			className="min-w-[150px] rounded-lg border-2 bg-card px-3 py-2 text-xs text-card-foreground shadow-sm"
			style={{
				borderColor: errored ? "var(--destructive, #ef4444)" : "var(--border)",
			}}
		>
			<Handle
				type="target"
				position={Position.Top}
				style={{ background: "var(--border)" }}
			/>
			<div className="flex items-center gap-1.5 font-medium">
				<span
					className="size-1.5 rounded-full"
					style={{
						background: errored ? "var(--destructive, #ef4444)" : "#22c55e",
					}}
				/>
				<span>{data.id}</span>
			</div>
			{data.role ? (
				<div className="mt-0.5 text-muted-foreground">{data.role}</div>
			) : null}
			<div className="mt-0.5 text-muted-foreground">
				{usd(data.costUsd)} · {Math.round(data.durationMs)}ms ·{" "}
				{data.tokens.toLocaleString()} tok
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

/** Render a run's trace as a read-only graph. Fetches the federated per-run trace on demand
 * (never stored) and is honest about the reachability states — poll-mode / unreachable / no-trace. */
export function RunGraph({
	instanceId,
	runId,
}: {
	instanceId: string;
	runId: string;
}) {
	const fetcher = useCallback(
		() => api.instanceRunTrace(instanceId, runId),
		[instanceId, runId],
	);
	const { data, error, loading } = useResource<RunTraceEnvelope>(
		`/instances/${instanceId}/runs/${runId}/trace`,
		fetcher,
	);

	const graph = useMemo(() => traceToGraph(data?.trace), [data?.trace]);
	const [nodes, setNodes, onNodesChange] = useNodesState<AgentNode>(
		graph.nodes as AgentNode[],
	);
	const [edges, setEdges, onEdgesChange] = useEdgesState(graph.edges as Edge[]);
	useEffect(() => {
		setNodes(graph.nodes as AgentNode[]);
		setEdges(graph.edges as Edge[]);
	}, [graph, setNodes, setEdges]);

	if (loading) {
		return <p className="p-6 text-sm text-muted-foreground">Loading trace…</p>;
	}
	if (error) {
		return (
			<p className="p-6 text-sm text-muted-foreground">
				Couldn’t reach the control plane: {error}
			</p>
		);
	}
	if (data && !data.reachable) {
		return (
			<p className="p-6 text-sm text-muted-foreground">
				{data.reason === "poll-mode"
					? "This is a poll-mode (Mode B) instance — the panel can’t pull its run traces."
					: "Instance unavailable — couldn’t fetch the run trace right now."}
			</p>
		);
	}
	if (!data?.trace || nodes.length === 0) {
		return (
			<p className="p-6 text-sm text-muted-foreground">
				No trace recorded for this run.
			</p>
		);
	}

	return (
		<div className="h-[440px] w-full">
			<ReactFlow
				nodes={nodes}
				edges={edges}
				onNodesChange={onNodesChange}
				onEdgesChange={onEdgesChange}
				nodeTypes={NODE_TYPES}
				fitView
				nodesDraggable={false}
				nodesConnectable={false}
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
