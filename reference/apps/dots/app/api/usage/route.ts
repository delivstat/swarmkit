import { type NextRequest, NextResponse } from "next/server";

// Proxy GET /api/usage → SwarmKit's GET /usage. Mirrors the connections proxy's shape: on
// unreachable upstream we return the empty-state shape so the page renders cleanly.

export const runtime = "nodejs";

interface UsageSummary {
	summary?: Record<string, unknown>;
	by_model?: Array<{
		model: string;
		input_tokens?: number;
		output_tokens?: number;
		cost_usd?: number;
		runs?: number;
	}>;
}

export async function GET(_req: NextRequest): Promise<Response> {
	const base = process.env.SWARMKIT_URL ?? "http://127.0.0.1:8000";
	try {
		const res = await fetch(`${base}/usage`, {
			headers: { accept: "application/json" },
		});
		if (!res.ok) return NextResponse.json({ summary: {}, by_model: [] });
		const body = (await res.json()) as UsageSummary;
		return NextResponse.json(body);
	} catch {
		return NextResponse.json({ summary: {}, by_model: [] });
	}
}
