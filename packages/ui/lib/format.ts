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

/** What a tool span was asked and what it answered, or null when the span is not a tool call.
 *
 * A trace span carried a tool's name and how many characters came back — never the arguments or
 * the result. So a waterfall could show that `search-wms-tables` ran for 800ms and not what it
 * looked for or found, which is the question the waterfall is usually opened to answer.
 */
export interface ToolDetail {
	name: string;
	arguments: Record<string, unknown>;
	result: string;
	resultLength: number;
	cached: boolean;
	truncated: boolean;
}

export function toolDetail(
	attributes: Record<string, unknown>,
): ToolDetail | null {
	const name = attributes["swarmkit.tool.name"];
	if (typeof name !== "string" || !name) return null;
	const args = attributes["swarmkit.tool.arguments"];
	const result = attributes["swarmkit.tool.result"];
	const resultLength = Number(attributes["swarmkit.tool.result_length"] ?? 0);
	const text = typeof result === "string" ? result : "";
	return {
		name,
		arguments:
			args && typeof args === "object" ? (args as Record<string, unknown>) : {},
		result: text,
		resultLength,
		// The trace keeps a bounded copy, so say when there was more rather than letting a reader
		// take a cut-off payload for the whole answer.
		truncated: resultLength > text.length,
		cached: attributes["swarmkit.tool.cached"] === true,
	};
}
