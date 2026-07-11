/** Display helpers for run cost + token counts. */

/** Format a USD cost. Sub-cent runs would round to `$0.00` at 2 decimals, so show 4 there; otherwise
 * 2. Non-positive / non-finite → `$0.00`. */
export function formatUsd(cost: number): string {
	if (!Number.isFinite(cost) || cost <= 0) return "$0.00";
	return `$${cost.toFixed(cost < 0.01 ? 4 : 2)}`;
}

/** Compact token count: 1234 → "1.2k", 1_200_000 → "1.2M". */
export function formatTokens(n: number): string {
	if (!Number.isFinite(n) || n <= 0) return "0";
	if (n < 1000) return String(Math.round(n));
	if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
	return `${(n / 1_000_000).toFixed(1)}M`;
}

/** A trace-span badge for a non-`model` executor (design executor-abstraction §5). Reads the
 * `swarmkit.executor.kind` / `swarmkit.executor.ref` span attributes and returns a short label like
 * `claude-code` or `claude-code · claude-opus-4-8`. Returns `null` for a plain model step (or a span
 * with no executor attribute — e.g. `topology.run`, `tool.call.*`), so only harness nodes get a chip. */
export function executorBadge(
	attributes: Record<string, unknown>,
): string | null {
	const kind = attributes["swarmkit.executor.kind"];
	if (typeof kind !== "string" || kind === "" || kind === "model") return null;
	const ref = attributes["swarmkit.executor.ref"];
	return typeof ref === "string" && ref !== "" ? `${kind} · ${ref}` : kind;
}

/** The span's recorded cost, from the `swarmkit.model.cost_usd` attribute; `0` when absent. */
export function spanCostUsd(attributes: Record<string, unknown>): number {
	const cost = attributes["swarmkit.model.cost_usd"];
	return typeof cost === "number" && Number.isFinite(cost) ? cost : 0;
}
