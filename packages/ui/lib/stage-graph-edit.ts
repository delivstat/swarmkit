// Pure structural edits on a StageGraph document (design/details/pipeline-editor-canvas.md).
//
// The one load-bearing idea: **connections in a pipeline are events, not pointers**. A forward arrow
// A→B is not stored — it is DERIVED from a shared signal (`A.success = S`, `S ∈ B.when`). An external
// entry is a `when` event no stage emits. Only loops are stored explicitly (in `loops[]`). So every
// canvas gesture here is a pure `(doc, args) → doc` mutation that writes to the honest home of each
// connection type, keeping the YAML authoritative. Every op deep-clones and preserves untouched
// fields (metadata, provenance, unknown stage keys) so a canvas edit is loss-free through
// dump → load (packages/ui invariant #1 — round-trip, no second source of truth). Kept pure (no
// React) so the composer wiring and its tests share one implementation.

/** A stage-graph document — loose; we read/write only the fields the canvas visualizes and preserve
 * everything else verbatim. */
export type StageGraphDocObj = Record<string, unknown>;

interface RawStage {
	id?: unknown;
	topology?: unknown;
	when?: unknown;
	success?: unknown;
	locks?: unknown;
	release_locks_on?: unknown;
	gate?: unknown;
	compensation?: unknown;
	[key: string]: unknown;
}

interface RawLoop {
	when?: unknown;
	to?: unknown;
	[key: string]: unknown;
}

/** Deep-clone the document and normalize `stages`/`loops` to arrays we can edit in place. The clone
 * carries every unknown key (metadata, provenance, future fields) untouched. */
function clone(doc: StageGraphDocObj | null | undefined): StageGraphDocObj {
	const base: StageGraphDocObj = doc ? structuredClone(doc) : {};
	if (!Array.isArray(base.stages)) base.stages = [];
	if (!Array.isArray(base.loops)) base.loops = [];
	return base;
}

function stagesOf(doc: StageGraphDocObj): RawStage[] {
	return (doc.stages as unknown[]).filter(
		(s): s is RawStage =>
			typeof s === "object" && s !== null && !Array.isArray(s),
	);
}

function loopsOf(doc: StageGraphDocObj): RawLoop[] {
	return (doc.loops as unknown[]).filter(
		(l): l is RawLoop =>
			typeof l === "object" && l !== null && !Array.isArray(l),
	);
}

function findStage(doc: StageGraphDocObj, id: string): RawStage | undefined {
	return stagesOf(doc).find((s) => s.id === id);
}

function stageIds(doc: StageGraphDocObj): string[] {
	return stagesOf(doc)
		.map((s) => (typeof s.id === "string" ? s.id : ""))
		.filter((s) => s.length > 0);
}

function asStrArray(v: unknown): string[] {
	return Array.isArray(v)
		? v.filter((x): x is string => typeof x === "string")
		: [];
}

/** Set a scalar field, or delete it when the value is empty (so clearing a gate/success removes the
 * key rather than leaving a null the schema does not want). */
function setOrClear(stage: RawStage, key: string, value: string | null): void {
	if (value && value.length > 0) stage[key] = value;
	else clearKey(stage, key);
}

/** Remove a key. A computed-member delete (not a static `delete obj.prop`) keeps the object shape
 * honest for round-trip fidelity without leaving a `null` the schema does not want. */
function clearKey(obj: Record<string, unknown>, key: string): void {
	delete obj[key];
}

/** Kebab-case slug from an arbitrary string (topology id, path, etc.). */
function slug(text: string): string {
	const parts = text.split("/");
	const last = parts[parts.length - 1] ?? text;
	return last
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-|-$/g, "");
}

/** A unique stage id derived from a topology ref, deduped against the existing stage ids
 * (`design`, then `design-2`, `design-3`, …). Falls back to `stage` for an unnameable topology. */
export function deriveStageId(doc: StageGraphDocObj, topology: string): string {
	const base = slug(topology) || "stage";
	const taken = new Set(stageIds(doc));
	if (!taken.has(base)) return base;
	for (let i = 2; ; i += 1) {
		const candidate = `${base}-${i}`;
		if (!taken.has(candidate)) return candidate;
	}
}

// ── Stages ──────────────────────────────────────────────────────────────────────────────────────

/** Drop a topology onto the canvas: append a stage bound to it, with a unique id. */
export function addStage(
	doc: StageGraphDocObj,
	topology: string,
	id?: string,
): StageGraphDocObj {
	const next = clone(doc);
	const stageId = id ?? deriveStageId(next, topology);
	if (findStage(next, stageId)) return next; // id already taken → no-op
	(next.stages as RawStage[]).push({ id: stageId, topology, when: [] });
	return next;
}

/** Rename a stage; rewrite any `loops[].to` that pointed at it (the one back-reference to an id).
 * No-op if the target id is empty, unchanged, missing, or would collide with another stage. */
export function renameStage(
	doc: StageGraphDocObj,
	oldId: string,
	newId: string,
): StageGraphDocObj {
	if (!newId || oldId === newId) return doc;
	const next = clone(doc);
	if (!findStage(next, oldId)) return doc;
	if (stageIds(next).includes(newId)) return doc; // collision
	const stage = findStage(next, oldId);
	if (stage) stage.id = newId;
	for (const loop of loopsOf(next)) if (loop.to === oldId) loop.to = newId;
	return next;
}

/** Remove a stage and any loops that re-enter it. `when` events elsewhere that referenced its
 * `success` simply become external (inferred) — left as-is. */
export function removeStage(
	doc: StageGraphDocObj,
	id: string,
): StageGraphDocObj {
	const next = clone(doc);
	if (!findStage(next, id)) return doc;
	next.stages = (next.stages as RawStage[]).filter((s) => s.id !== id);
	next.loops = (next.loops as RawLoop[]).filter((l) => l.to !== id);
	return next;
}

/** Bind a different topology to a stage. */
export function setStageTopology(
	doc: StageGraphDocObj,
	id: string,
	topology: string,
): StageGraphDocObj {
	const next = clone(doc);
	const stage = findStage(next, id);
	if (!stage) return doc;
	stage.topology = topology;
	return next;
}

// ── Signal edges (forward flow) ─────────────────────────────────────────────────────────────────

/** Draw a signal edge A→B: ensure `A.success = S` (reusing A's existing signal, else `signal`, else
 * the default `<A.id>.done`) and add `S` to `B.when`. No-op for a self-edge or a missing endpoint. */
export function drawSignalEdge(
	doc: StageGraphDocObj,
	sourceId: string,
	targetId: string,
	signal?: string,
): StageGraphDocObj {
	if (sourceId === targetId) return doc;
	const next = clone(doc);
	const source = findStage(next, sourceId);
	const target = findStage(next, targetId);
	if (!source || !target) return doc;
	const existing = typeof source.success === "string" ? source.success : "";
	const s = existing || signal || `${sourceId}.done`;
	source.success = s;
	const when = asStrArray(target.when);
	if (!when.includes(s)) target.when = [...when, s];
	else target.when = when;
	return next;
}

/** Whether any stage's `when` or any `loops[].when` consumes the signal `s`. Used to decide if a
 * `success` is now dangling after an edge delete. */
function isConsumed(doc: StageGraphDocObj, s: string): boolean {
	for (const stage of stagesOf(doc))
		if (asStrArray(stage.when).includes(s)) return true;
	for (const loop of loopsOf(doc)) if (loop.when === s) return true;
	return false;
}

/** Delete a signal edge A→B: remove A's signal from `B.when`, then clear `A.success` if no stage (or
 * loop) still consumes it. No-op if A emits nothing. */
export function deleteSignalEdge(
	doc: StageGraphDocObj,
	sourceId: string,
	targetId: string,
): StageGraphDocObj {
	const next = clone(doc);
	const source = findStage(next, sourceId);
	const target = findStage(next, targetId);
	if (!source || !target) return doc;
	const s = typeof source.success === "string" ? source.success : "";
	if (!s) return doc;
	target.when = asStrArray(target.when).filter((w) => w !== s);
	if (!isConsumed(next, s)) clearKey(source, "success");
	return next;
}

// ── External entries (inbound webhooks) ─────────────────────────────────────────────────────────

/** Add an external entry to a stage: an event in its `when` that no stage emits (a CI/Jira/Git
 * webhook). External-vs-internal is INFERRED (design decision) — no schema field is written. */
export function addExternalEntry(
	doc: StageGraphDocObj,
	stageId: string,
	event: string,
): StageGraphDocObj {
	if (!event) return doc;
	const next = clone(doc);
	const stage = findStage(next, stageId);
	if (!stage) return doc;
	const when = asStrArray(stage.when);
	if (!when.includes(event)) stage.when = [...when, event];
	return next;
}

/** Remove an event from a stage's `when` (the delete for an external entry, or any listened event). */
export function removeWhenEvent(
	doc: StageGraphDocObj,
	stageId: string,
	event: string,
): StageGraphDocObj {
	const next = clone(doc);
	const stage = findStage(next, stageId);
	if (!stage) return doc;
	const when = asStrArray(stage.when);
	if (!when.includes(event)) return doc;
	stage.when = when.filter((w) => w !== event);
	return next;
}

// ── Loops (defect re-entry) ─────────────────────────────────────────────────────────────────────

/** Append a loop `{ when: E, to: <stage> }` — an explicit back-edge on event `E`. No-op if the
 * target stage is missing or the identical loop already exists. */
export function addLoop(
	doc: StageGraphDocObj,
	to: string,
	when: string,
): StageGraphDocObj {
	if (!when) return doc;
	const next = clone(doc);
	if (!findStage(next, to)) return doc;
	const exists = loopsOf(next).some((l) => l.when === when && l.to === to);
	if (exists) return doc;
	(next.loops as RawLoop[]).push({ when, to });
	return next;
}

/** Remove the loop `{ when, to }`. */
export function deleteLoop(
	doc: StageGraphDocObj,
	to: string,
	when: string,
): StageGraphDocObj {
	const next = clone(doc);
	const before = (next.loops as RawLoop[]).length;
	next.loops = (next.loops as RawLoop[]).filter(
		(l) => !(l.when === when && l.to === to),
	);
	if ((next.loops as RawLoop[]).length === before) return doc;
	return next;
}

/** Retarget an existing loop's trigger event (edit its `when`), keeping its `to`. */
export function setLoopWhen(
	doc: StageGraphDocObj,
	to: string,
	oldWhen: string,
	newWhen: string,
): StageGraphDocObj {
	if (!newWhen || oldWhen === newWhen) return doc;
	const next = clone(doc);
	const loop = loopsOf(next).find((l) => l.when === oldWhen && l.to === to);
	if (!loop) return doc;
	if (loopsOf(next).some((l) => l.when === newWhen && l.to === to)) return doc; // collision
	loop.when = newWhen;
	return next;
}

// ── Per-stage configuration (node properties, not connections) ──────────────────────────────────

/** Set (or clear, on null) the stage's Funnel gate ref. */
export function setGate(
	doc: StageGraphDocObj,
	stageId: string,
	gate: string | null,
): StageGraphDocObj {
	const next = clone(doc);
	const stage = findStage(next, stageId);
	if (!stage) return doc;
	setOrClear(stage, "gate", gate);
	return next;
}

/** Set (or clear, on null) the stage's compensation topology ref. */
export function setCompensation(
	doc: StageGraphDocObj,
	stageId: string,
	compensation: string | null,
): StageGraphDocObj {
	const next = clone(doc);
	const stage = findStage(next, stageId);
	if (!stage) return doc;
	setOrClear(stage, "compensation", compensation);
	return next;
}

/** Set (or clear, on null) the signal whose arrival releases this stage's locks. */
export function setReleaseLocksOn(
	doc: StageGraphDocObj,
	stageId: string,
	event: string | null,
): StageGraphDocObj {
	const next = clone(doc);
	const stage = findStage(next, stageId);
	if (!stage) return doc;
	setOrClear(stage, "release_locks_on", event);
	return next;
}

/** Add a lock (integration-contract id) held across the stage's run. No-op on a dup. */
export function addLock(
	doc: StageGraphDocObj,
	stageId: string,
	lock: string,
): StageGraphDocObj {
	if (!lock) return doc;
	const next = clone(doc);
	const stage = findStage(next, stageId);
	if (!stage) return doc;
	const locks = asStrArray(stage.locks);
	if (locks.includes(lock)) return doc;
	stage.locks = [...locks, lock];
	return next;
}

/** Remove a lock. Drops the `locks` key entirely when it empties. */
export function removeLock(
	doc: StageGraphDocObj,
	stageId: string,
	lock: string,
): StageGraphDocObj {
	const next = clone(doc);
	const stage = findStage(next, stageId);
	if (!stage) return doc;
	const locks = asStrArray(stage.locks);
	if (!locks.includes(lock)) return doc;
	const remaining = locks.filter((l) => l !== lock);
	if (remaining.length > 0) stage.locks = remaining;
	else clearKey(stage, "locks");
	return next;
}
