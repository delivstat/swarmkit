// Pure funnel → graph helpers for the funnel pipeline canvas (design/details/gate-funnel.md).
//
// A Funnel is a FIXED-topology quality gate: draft -> validate -> judge -> review -> approve, with a
// bounded retry loop back to draft and retry-exhaustion escalating to the human `approve` layer —
// the only exit. The control flow is compiler-owned and fixed; the editor CONFIGURES each layer and
// TOGGLES the optional ones (validate/judge/review), it never draws nodes or rewires edges. So the
// node rack is invariant (all six layers, always) — a toggled-off layer renders inactive, not
// removed. Kept pure (no React) so it is unit-testable and reused by the canvas component.

/** A layer of the fixed funnel pipeline. `draft` (the drafter's output) and `done` (released) are
 * structural anchors; `approve` is the required human gate; the rest are optional. */
export type FunnelLayerKey =
	| "draft"
	| "validate"
	| "judge"
	| "review"
	| "approve"
	| "done";

/** The optional, user-toggleable layers, in pipeline order. */
export const OPTIONAL_LAYERS = ["validate", "judge", "review"] as const;
export type OptionalLayerKey = (typeof OPTIONAL_LAYERS)[number];

/** Every layer in fixed pipeline order — the invariant left-to-right rack. */
export const FUNNEL_LAYERS: FunnelLayerKey[] = [
	"draft",
	"validate",
	"judge",
	"review",
	"approve",
	"done",
];

const LABELS: Record<FunnelLayerKey, string> = {
	draft: "draft",
	validate: "validate",
	judge: "judge",
	review: "review",
	approve: "approve",
	done: "done",
};

/** A funnel document (or a staged draft of one) — loose, we only read the layer keys. */
export type FunnelDoc = Record<string, unknown> | null | undefined;

export interface FunnelNodeData {
	key: FunnelLayerKey;
	label: string;
	/** Whether this layer runs. `draft`/`approve`/`done` are always active; optional layers depend
	 * on whether the funnel document carries their block. */
	present: boolean;
	optional: boolean;
	required: boolean;
	/** Whether a present layer carries any configuration yet (an empty toggled-on block reads as
	 * "needs config"). */
	configured: boolean;
	[k: string]: unknown; // React Flow's node data is an open record
}

export interface FunnelGraphNode {
	id: FunnelLayerKey;
	type: "layer";
	position: { x: number; y: number };
	data: FunnelNodeData;
}

export type FunnelEdgeKind = "forward" | "retry" | "escalate";

export interface FunnelGraphEdge {
	id: string;
	source: FunnelLayerKey;
	target: FunnelLayerKey;
	kind: FunnelEdgeKind;
	label?: string;
}

export interface FunnelGraph {
	nodes: FunnelGraphNode[];
	edges: FunnelGraphEdge[];
}

const H_GAP = 200;

/** Whether an optional layer is present in a funnel document (its key holds an object). */
export function layerPresent(
	funnel: FunnelDoc,
	key: OptionalLayerKey,
): boolean {
	if (!funnel) return false;
	const v = funnel[key];
	return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Whether a (present) layer carries any configuration keys. Structural anchors are always "set". */
function isConfigured(funnel: FunnelDoc, key: FunnelLayerKey): boolean {
	if (key === "draft" || key === "done") return true;
	if (!funnel) return false;
	const v = funnel[key];
	if (typeof v !== "object" || v === null) return false;
	return Object.keys(v as Record<string, unknown>).length > 0;
}

/**
 * Project a funnel document onto the fixed pipeline graph.
 *
 * Nodes: all six layers, always (the topology is fixed) — `data.present` marks which are active.
 * Edges depend on the active set so the live path reads continuously:
 *  - `forward`: chains the active layers in order (draft → …active optional… → approve → done).
 *  - `retry`:  the last active automated layer → draft (the bounded retry loop).
 *  - `escalate`: judge → approve when both judge and review are active (retry-exhaustion bypasses
 *    review straight to the human gate). When review is off, judge's forward edge already lands on
 *    approve, so no separate escalate edge is drawn.
 */
export function funnelToGraph(funnel: FunnelDoc): FunnelGraph {
	const activeOptional = OPTIONAL_LAYERS.filter((k) => layerPresent(funnel, k));
	const active: FunnelLayerKey[] = [
		"draft",
		...activeOptional,
		"approve",
		"done",
	];
	const activeSet = new Set<FunnelLayerKey>(active);

	const nodes: FunnelGraphNode[] = FUNNEL_LAYERS.map((key, i) => ({
		id: key,
		type: "layer" as const,
		position: { x: i * H_GAP, y: 0 },
		data: {
			key,
			label: LABELS[key],
			present: activeSet.has(key),
			optional: (OPTIONAL_LAYERS as readonly string[]).includes(key),
			required: key === "approve",
			configured: isConfigured(funnel, key),
		},
	}));

	const edges: FunnelGraphEdge[] = [];

	// Forward chain over the active layers.
	for (let i = 0; i + 1 < active.length; i += 1) {
		const source = active[i];
		const target = active[i + 1];
		if (!source || !target) continue;
		edges.push({
			id: `${source}->${target}`,
			source,
			target,
			kind: "forward",
			...(target === "approve" ? { label: "pass · exhausted" } : {}),
		});
	}

	// Bounded retry loop: the last active automated layer routes back to draft.
	const lastAuto = activeOptional[activeOptional.length - 1];
	if (lastAuto) {
		edges.push({
			id: `${lastAuto}->draft:retry`,
			source: lastAuto,
			target: "draft",
			kind: "retry",
			label: "retry",
		});
	}

	// Escalation: judge exhaustion bypasses review straight to the human gate.
	if (activeSet.has("judge") && activeSet.has("review")) {
		edges.push({
			id: "judge->approve:escalate",
			source: "judge",
			target: "approve",
			kind: "escalate",
			label: "escalate",
		});
	}

	return { nodes, edges };
}
