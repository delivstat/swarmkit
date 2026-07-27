// Pure grouping helpers for the governed-memory browser (design/details/governed-memory.md).
// A flat memory list is grouped two ways for the UI's tree and category views. Kept out of the
// component so it is unit-testable.

import type { MemoryItem } from "./types";

/** One branch of the subject tree: a subject and its facts (attributes), attribute-sorted. */
export interface MemorySubjectGroup {
	subject: string;
	items: MemoryItem[];
}

/** One category bucket: a memory type and its facts. */
export interface MemoryTypeGroup {
	type: string;
	items: MemoryItem[];
}

function bySubjectThenAttribute(a: MemoryItem, b: MemoryItem): number {
	return (
		a.subject.localeCompare(b.subject) || a.attribute.localeCompare(b.attribute)
	);
}

/** Group memories into a subject → attributes tree, subjects and attributes sorted. */
export function buildMemoryTree(memories: MemoryItem[]): MemorySubjectGroup[] {
	const bySubject = new Map<string, MemoryItem[]>();
	for (const m of memories) {
		const list = bySubject.get(m.subject);
		if (list) list.push(m);
		else bySubject.set(m.subject, [m]);
	}
	return [...bySubject.entries()]
		.map(([subject, items]) => ({
			subject,
			items: [...items].sort((a, b) => a.attribute.localeCompare(b.attribute)),
		}))
		.sort((a, b) => a.subject.localeCompare(b.subject));
}

/** Group memories by type (semantic/profile/…), each bucket subject/attribute-sorted. */
export function groupByType(memories: MemoryItem[]): MemoryTypeGroup[] {
	const byType = new Map<string, MemoryItem[]>();
	for (const m of memories) {
		const list = byType.get(m.type);
		if (list) list.push(m);
		else byType.set(m.type, [m]);
	}
	return [...byType.entries()]
		.map(([type, items]) => ({
			type,
			items: [...items].sort(bySubjectThenAttribute),
		}))
		.sort((a, b) => a.type.localeCompare(b.type));
}

/** Stable key for a memory item (matches the store's `(subject, attribute)` key). */
export function memoryKey(
	m: Pick<MemoryItem, "subject" | "attribute">,
): string {
	return `${m.subject}::${m.attribute}`;
}
