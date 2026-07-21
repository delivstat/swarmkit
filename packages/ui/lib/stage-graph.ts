// Pure stage-graph → graph helpers for the pipeline canvas (design/details/pipeline-controller.md).
//
// A StageGraph is the *pipeline as data*: an ordered set of bounded stages a controller sequences as
// a saga. Unlike a Funnel (a FIXED gate pipeline), a stage graph is a genuine DAG — stages are wired
// by SIGNAL, not position: a stage's `success` signal is the entry event (`when`) of the next
// stage(s), and `loops` are cross-stage edges (the defect cycle). So the canvas is a READ-ONLY
// visualization derived from those signal matches — it draws the DAG, it does not let you rewire it
// (editing happens in the form/yaml modes, round-tripped through the artifact). Kept pure (no React)
// so it is unit-testable and reused by the canvas component.

/** A stage-graph document (or a staged draft of one) — loose, we only read the fields we render. */
export type StageGraphDoc = Record<string, unknown> | null | undefined;

/** One stage of the pipeline, normalized from the (loose) document. */
export interface StageSpec {
	id: string;
	/** The topology this stage kicks (x-swarmkit-ref: topology). Empty while being authored. */
	topology: string | null;
	/** Entry event(s): external enterprise events or a prior stage's `success` signal. */
	when: string[];
	/** The signal emitted on clean completion — drives the next stage's `when`. */
	success: string | null;
	/** Integration-contract lock ids held across the run. */
	locks: string[];
	/** The signal whose arrival releases this stage's locks. */
	releaseLocksOn: string | null;
	/** A Funnel gate (x-swarmkit-ref: funnel) the run parks on. */
	gate: string | null;
	/** Topology run to unwind this stage on cancellation (x-swarmkit-ref: topology). */
	compensation: string | null;
}

/** A cross-stage loop edge (the defect cycle): an inbound event routes to a stage. */
export interface StageLoop {
	when: string;
	to: string;
}

function isRecord(v: unknown): v is Record<string, unknown> {
	return typeof v === "object" && v !== null && !Array.isArray(v);
}

function asString(v: unknown): string | null {
	return typeof v === "string" && v.length > 0 ? v : null;
}

function asStringArray(v: unknown): string[] {
	return Array.isArray(v)
		? v.filter((x): x is string => typeof x === "string")
		: [];
}

/** Parse the (loose) stages array into normalized specs, skipping malformed entries. */
export function readStages(doc: StageGraphDoc): StageSpec[] {
	const raw = doc && Array.isArray(doc.stages) ? doc.stages : [];
	return raw.filter(isRecord).map((s) => ({
		id: asString(s.id) ?? "",
		topology: asString(s.topology),
		when: asStringArray(s.when),
		success: asString(s.success),
		locks: asStringArray(s.locks),
		releaseLocksOn: asString(s.release_locks_on),
		gate: asString(s.gate),
		compensation: asString(s.compensation),
	}));
}

/** Parse the (loose) loops array into normalized {when, to} edges, skipping malformed entries. */
export function readLoops(doc: StageGraphDoc): StageLoop[] {
	const raw = doc && Array.isArray(doc.loops) ? doc.loops : [];
	return raw.filter(isRecord).flatMap((l) => {
		const when = asString(l.when);
		const to = asString(l.to);
		return when && to ? [{ when, to }] : [];
	});
}

export interface StageNodeData {
	id: string;
	topology: string | null;
	when: string[];
	success: string | null;
	gate: string | null;
	locks: string[];
	compensation: string | null;
	/** No incoming forward edge — a pipeline entry point (kicked by an external event). */
	isEntry: boolean;
	[k: string]: unknown; // React Flow's node data is an open record
}

export interface StageGraphNode {
	id: string;
	type: "stage";
	position: { x: number; y: number };
	data: StageNodeData;
}

export type StageEdgeKind = "forward" | "loop";

export interface StageGraphEdge {
	id: string;
	source: string;
	target: string;
	kind: StageEdgeKind;
	label: string;
}

export interface StageGraphProjection {
	nodes: StageGraphNode[];
	edges: StageGraphEdge[];
	/** Loops whose trigger event no stage emits as `success` — external re-entry, no source node to
	 * anchor an edge on. Surfaced as annotations rather than drawn. */
	externalLoops: StageLoop[];
}

// Layout spacing (px): a column per forward-edge depth, stages stacked down each column.
const H_GAP = 240;
const V_GAP = 120;

function push<T>(map: Map<string, T[]>, key: string, value: T): void {
	const arr = map.get(key);
	if (arr) arr.push(value);
	else map.set(key, [value]);
}

/**
 * Project a stage-graph document onto a left→right DAG.
 *
 * Nodes: one per stage, laid out in columns by longest-path depth over the forward edges.
 * Edges:
 *  - `forward`: stage A → stage B when A's `success` signal is one of B's `when` entry events.
 *  - `loop`:    a `loops` entry whose `when` a stage emits as `success` → its `to` stage (the defect
 *    cycle). A loop whose `when` no stage emits is external re-entry — returned in `externalLoops`.
 */
export function stageGraphToGraph(doc: StageGraphDoc): StageGraphProjection {
	const stages = readStages(doc).filter((s) => s.id);
	const loops = readLoops(doc);
	const ids = new Set(stages.map((s) => s.id));

	// signal -> stage ids that emit it as `success`.
	const emitters = new Map<string, string[]>();
	for (const s of stages) if (s.success) push(emitters, s.success, s.id);

	const edges: StageGraphEdge[] = [];
	const seen = new Set<string>();
	const indeg = new Map<string, number>(stages.map((s) => [s.id, 0]));
	const preds = new Map<string, string[]>(stages.map((s) => [s.id, []]));

	const addEdge = (e: StageGraphEdge) => {
		if (seen.has(e.id)) return;
		seen.add(e.id);
		edges.push(e);
	};

	// Forward edges: a prior stage's `success` matching a later stage's `when`.
	for (const b of stages) {
		for (const w of b.when) {
			for (const aId of emitters.get(w) ?? []) {
				if (aId === b.id) continue; // ignore a stage that re-triggers itself
				const id = `${aId}->${b.id}:${w}`;
				if (seen.has(id)) continue;
				addEdge({ id, source: aId, target: b.id, kind: "forward", label: w });
				indeg.set(b.id, (indeg.get(b.id) ?? 0) + 1);
				preds.get(b.id)?.push(aId);
			}
		}
	}

	// Longest-path depth over the forward DAG (entries at 0), with a cycle guard.
	const depth = new Map<string, number>();
	const visiting = new Set<string>();
	const depthOf = (id: string): number => {
		const memo = depth.get(id);
		if (memo !== undefined) return memo;
		if (visiting.has(id)) return 0; // defensive: a signal cycle in the forward edges
		visiting.add(id);
		const ps = preds.get(id) ?? [];
		const d = ps.length === 0 ? 0 : Math.max(...ps.map(depthOf)) + 1;
		visiting.delete(id);
		depth.set(id, d);
		return d;
	};
	for (const s of stages) depthOf(s.id);

	// Stack stages within each depth column, in declaration order.
	const rowInColumn = new Map<string, number>();
	const columnCount = new Map<number, number>();
	for (const s of stages) {
		const d = depth.get(s.id) ?? 0;
		const row = columnCount.get(d) ?? 0;
		rowInColumn.set(s.id, row);
		columnCount.set(d, row + 1);
	}

	const nodes: StageGraphNode[] = stages.map((s) => ({
		id: s.id,
		type: "stage" as const,
		position: {
			x: (depth.get(s.id) ?? 0) * H_GAP,
			y: (rowInColumn.get(s.id) ?? 0) * V_GAP,
		},
		data: {
			id: s.id,
			topology: s.topology,
			when: s.when,
			success: s.success,
			gate: s.gate,
			locks: s.locks,
			compensation: s.compensation,
			isEntry: (indeg.get(s.id) ?? 0) === 0,
		},
	}));

	// Loop edges: resolve the source by which stage emits the loop's `when` signal.
	const externalLoops: StageLoop[] = [];
	for (const loop of loops) {
		const sources = emitters.get(loop.when) ?? [];
		const targetExists = ids.has(loop.to);
		if (sources.length === 0) {
			externalLoops.push(loop);
			continue;
		}
		if (!targetExists) continue; // dangling target — nothing to draw
		for (const src of sources) {
			addEdge({
				id: `loop:${src}->${loop.to}:${loop.when}`,
				source: src,
				target: loop.to,
				kind: "loop",
				label: loop.when,
			});
		}
	}

	return { nodes, edges, externalLoops };
}
