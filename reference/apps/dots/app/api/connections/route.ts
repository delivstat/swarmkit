import { type NextRequest, NextResponse } from "next/server";

// Proxy GET /api/connections → SwarmKit's GET /api/oauth/my-credentials. Returns the raw
// payload so the page can render the same shape SwarmKit does. On any failure we return an
// empty list rather than 5xx — the mock-runtime demo does not expose this endpoint, and the
// connections page should still render a sensible empty state in that case.

export const runtime = "nodejs";

interface MyCredentials {
	rows?: Array<{
		credential_id: string;
		display_name?: string;
		connected?: boolean | null;
		used_by?: string[];
	}>;
}

export async function GET(_req: NextRequest): Promise<Response> {
	const base = process.env.SWARMKIT_URL ?? "http://127.0.0.1:8000";
	try {
		const res = await fetch(`${base}/api/oauth/my-credentials`, {
			headers: { accept: "application/json" },
		});
		if (!res.ok) return NextResponse.json({ rows: [] });
		const body = (await res.json()) as MyCredentials;
		return NextResponse.json(body);
	} catch {
		return NextResponse.json({ rows: [] });
	}
}
