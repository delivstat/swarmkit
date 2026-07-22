// Pure helpers for the Contracts surface + the pipeline's contract-contention overlay
// (design/details/contract-registry.md).
//
// A Contract is an integration contract — the agreed interface between two (or more) applications,
// identified by id. It is the checked, pickable vocabulary a StageGraph stage's `locks` reference
// (instead of free strings). The contract is not executed; it makes a lock id real and carries which
// apps it binds (`parties`). These helpers parse the (loose) artifact document into a normalized
// projection and label a contract by its parties — kept pure (no React) so they are unit-testable and
// reused by the contracts page and the pipeline canvas overlay.

/** A contract document (or a staged draft of one) — loose, we only read the fields we render. */
export type ContractDoc = Record<string, unknown> | null | undefined;

/** One integration contract, normalized from the (loose) document. */
export interface ContractSpec {
	id: string;
	name: string | null;
	/** The applications this contract binds (>= 2 for a valid contract). Free strings. */
	parties: string[];
	/** Optional pointer to where the interface itself lives (an API/event schema). */
	interface: string | null;
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

/** Parse a (loose) Contract document into a normalized spec. The id/name live under `metadata`; the
 * `parties`/`interface` are top-level. Missing/malformed fields degrade to empty rather than throw. */
export function readContract(doc: ContractDoc): ContractSpec {
	const meta = doc && isRecord(doc.metadata) ? doc.metadata : {};
	return {
		id: asString(meta.id) ?? "",
		name: asString(meta.name),
		parties: asStringArray(doc?.parties),
		interface: asString(doc?.interface),
	};
}

/** A one-line label for a contract: its parties joined with "↔" (the interface it binds), falling
 * back to the id when no parties are known. Used to annotate a lock in the pipeline overlay. */
export function contractLabel(id: string, parties: string[]): string {
	const named = parties.filter((p) => p.length > 0);
	return named.length >= 2 ? `${id} (${named.join(" ↔ ")})` : id;
}

/** Index a set of contract specs by id → parties, for the pipeline overlay's party labels. Later
 * entries win on a duplicate id. Specs with no id are skipped. */
export function partiesById(specs: ContractSpec[]): Record<string, string[]> {
	const out: Record<string, string[]> = {};
	for (const s of specs) if (s.id) out[s.id] = s.parties;
	return out;
}
