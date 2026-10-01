// Usage/cost shape (design/details/dot-app.md §3.7 Usage & cost). Projected from the runtime's
// audit aggregation surface; DOT falls back to paging /audit and aggregating in-memory if the
// aggregate endpoint isn't available (fine at owner-only scale).

export interface UsageTotals {
	tokensIn: number;
	tokensOut: number;
	costUsd: number;
}

export interface UsageSummary {
	today: UsageTotals;
	monthToDate: UsageTotals;
	last30Days: UsageTotals;
}

export interface UsageDaily {
	days: {
		date: string;
		tokensIn: number;
		tokensOut: number;
		costUsd: number;
	}[];
}

export interface UsageBreakdown {
	by: "provider" | "topology";
	rows: {
		label: string;
		tokensIn: number;
		tokensOut: number;
		costUsd: number;
	}[];
}

export function formatTokens(n: number): string {
	if (n < 1000) return String(n);
	if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
	return `${(n / 1_000_000).toFixed(2)}M`;
}

export function formatUsd(n: number): string {
	return n >= 100 ? `$${n.toFixed(0)}` : `$${n.toFixed(2)}`;
}
