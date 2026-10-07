import { type NextRequest, NextResponse } from "next/server";

import { correlationIdFor, findSpace } from "@/lib/spaces.config";

// GET /api/spaces/<spaceId>/activity — proxy to SwarmKit's /jobs/history?correlation_id=...
// filtered by this Space's correlation_id (space:<id>). One timeline per Space; the
// view renders it as the Activity feed on the left side of the Space page.
//
// Why not /audit directly? /jobs/history is correlation-filtered natively; /audit is
// per-run only. Jobs is the right grain for the Space view (one row per run);
// drilling into a run goes to the Portal.

export const runtime = "nodejs";

export async function GET(
	_req: NextRequest,
	ctx: { params: Promise<{ spaceId: string }> },
): Promise<Response> {
	const { spaceId } = await ctx.params;
	const space = await findSpace(spaceId);
	if (!space) return NextResponse.json({ error: "space_not_found" }, { status: 404 });

	const base = process.env.SWARMKIT_URL ?? "http://127.0.0.1:8000";
	const corr = correlationIdFor(spaceId);
	const upstream = await fetch(`${base}/jobs/history?correlation_id=${encodeURIComponent(corr)}`, {
		cache: "no-store",
	});
	if (!upstream.ok) {
		return NextResponse.json({ error: "upstream_error", status: upstream.status }, { status: 502 });
	}
	const jobs = (await upstream.json()) as unknown;
	return NextResponse.json({ correlation_id: corr, jobs });
}
