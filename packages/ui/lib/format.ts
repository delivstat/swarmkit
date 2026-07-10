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
