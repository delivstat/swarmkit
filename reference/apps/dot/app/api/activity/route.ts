import { type ActivityEntry, DOT_TOPOLOGIES } from "@/lib/activity";
import { COOKIE_NAME, verifySession } from "@/lib/session";
import {
	type AuditEntry,
	SwarmKitError,
	clientFromEnv,
} from "@/lib/swarmkit-client";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export async function GET(req: Request): Promise<Response> {
	const store = await cookies();
	const payload = await verifySession(store.get(COOKIE_NAME)?.value);
	if (!payload) {
		return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
	}
	const client = clientFromEnv(payload.owner);
	if (!client) {
		return NextResponse.json(
			{ error: "runtime not configured" },
			{ status: 503 },
		);
	}
	const cursor = new URL(req.url).searchParams.get("cursor") ?? undefined;
	try {
		const res = await client.listRuns({
			topologies: [...DOT_TOPOLOGIES],
			cursor,
			limit: 20,
		});
		return NextResponse.json({
			entries: res.entries.map(toActivity),
			nextCursor: res.next_cursor,
		});
	} catch (e) {
		if (e instanceof SwarmKitError) {
			return NextResponse.json(
				{ error: "runtime_error", detail: e.message },
				{ status: 502 },
			);
		}
		throw e;
	}
}

function toActivity(a: AuditEntry): ActivityEntry {
	return {
		runId: a.run_id,
		topologyId: a.topology_id,
		startedAt: a.started_at,
		elapsedMs: a.elapsed_ms,
		status: a.status,
		summary: a.summary,
	};
}
