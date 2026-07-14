// Pure structural edits on a topology's RAW agent tree (design/details/topology-canvas.md).
// The canvas edits the raw `agents.root` object and round-trips through YAML — never a second source
// of truth (packages/ui invariant #1). Every op deep-clones and preserves untouched fields (prompt,
// model, skills, archetype, …) so a canvas edit is loss-free through dump → load. Kept pure so both
// the composer wiring and its tests share one implementation.

/** A raw agent node as it appears under `agents.root` in a topology YAML. Open-ended: only
 * `id`/`children` are structural; everything else (role, archetype, prompt, model, skills…) is
 * preserved verbatim. */
export interface RawAgent {
	id: string;
	children?: RawAgent[];
	[key: string]: unknown;
}

function clone(root: RawAgent): RawAgent {
	return structuredClone(root);
}

function find(
	node: RawAgent,
	id: string,
	parent: RawAgent | null = null,
): { node: RawAgent; parent: RawAgent | null } | null {
	if (node.id === id) return { node, parent };
	for (const child of node.children ?? []) {
		const hit = find(child, id, node);
		if (hit) return hit;
	}
	return null;
}

/** True if `ancestorId` is `id` or an ancestor of it — used to refuse cycle-forming reparents. */
function isAncestor(root: RawAgent, ancestorId: string, id: string): boolean {
	const found = find(root, ancestorId);
	return found ? find(found.node, id) !== null : false;
}

/** Add `child` under `parentId`. No-op (returns the tree unchanged) if the parent is missing or the
 * child id already exists. */
export function addChild(
	root: RawAgent,
	parentId: string,
	child: RawAgent,
): RawAgent {
	if (find(root, child.id)) return root;
	const next = clone(root);
	const parent = find(next, parentId)?.node;
	if (!parent) return root;
	parent.children = [...(parent.children ?? []), child];
	return next;
}

/** Remove the agent (and its subtree) by id. The root cannot be removed (returns unchanged). */
export function removeAgent(root: RawAgent, id: string): RawAgent {
	if (root.id === id) return root; // never remove the root
	const next = clone(root);
	const hit = find(next, id);
	if (!hit || !hit.parent) return root;
	hit.parent.children = (hit.parent.children ?? []).filter((c) => c.id !== id);
	return next;
}

/** Add `skill` to an agent's `skills` list. No-op if the agent is missing or already has it. */
export function addSkill(
	root: RawAgent,
	agentId: string,
	skill: string,
): RawAgent {
	const next = clone(root);
	const node = find(next, agentId)?.node;
	if (!node) return root;
	const skills = Array.isArray(node.skills) ? (node.skills as string[]) : [];
	if (skills.includes(skill)) return root;
	node.skills = [...skills, skill];
	return next;
}

/** Remove `skill` from an agent's `skills` list. No-op if the agent or the skill is absent. */
export function removeSkill(
	root: RawAgent,
	agentId: string,
	skill: string,
): RawAgent {
	const next = clone(root);
	const node = find(next, agentId)?.node;
	if (!node || !Array.isArray(node.skills)) return root;
	const skills = node.skills as string[];
	if (!skills.includes(skill)) return root;
	node.skills = skills.filter((s) => s !== skill);
	return next;
}

/** Re-parent an agent (draw a delegation edge from `newParentId` to `id`). Refused (returns
 * unchanged) for the root, an unknown node/parent, a no-op move, or a move that would form a cycle
 * (making a node a child of its own descendant). */
export function reparent(
	root: RawAgent,
	id: string,
	newParentId: string,
): RawAgent {
	if (id === root.id || id === newParentId) return root;
	if (isAncestor(root, id, newParentId)) return root; // would create a cycle
	const next = clone(root);
	const moving = find(next, id);
	const target = find(next, newParentId)?.node;
	if (!moving || !moving.parent || !target) return root;
	if (moving.parent.id === newParentId) return root; // already there
	moving.parent.children = (moving.parent.children ?? []).filter(
		(c) => c.id !== id,
	);
	target.children = [...(target.children ?? []), moving.node];
	return next;
}
