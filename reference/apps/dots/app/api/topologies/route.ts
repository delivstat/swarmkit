import { type NextRequest, NextResponse } from "next/server";

// GET /api/topologies — forward to swarmkit serve's /topologies so the /dots/new form can
// populate its dropdown from topologies that actually exist in the workspace. Empty list
// when the runtime is unreachable (mock-runtime doesn't expose /topologies).

export const runtime = "nodejs";

interface TopologiesPayload {
	topologies?: Array<{ name: string } | string>;
}

export async function GET(_req: NextRequest): Promise<Response> {
	const base = process.env.SWARMKIT_URL ?? "http://127.0.0.1:8000";
	try {
		const res = await fetch(`${base}/topologies`, {
			headers: { accept: "application/json" },
		});
		if (!res.ok) return NextResponse.json({ names: [] });
		const body = (await res.json()) as TopologiesPayload | string[];
		const list = Array.isArray(body) ? body : (body.topologies ?? []);
		const names = list.map((entry) => (typeof entry === "string" ? entry : entry.name));
		return NextResponse.json({ names });
	} catch {
		return NextResponse.json({ names: [] });
	}
}
