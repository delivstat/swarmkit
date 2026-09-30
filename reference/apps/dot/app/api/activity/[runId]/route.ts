import type { ActivityDetail } from "@/lib/activity";
import { COOKIE_NAME, verifySession } from "@/lib/session";
import {
	type AuditDetail,
	SwarmKitError,
	clientFromEnv,
} from "@/lib/swarmkit-client";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export async function GET(
	_req: Request,
	{ params }: { params: Promise<{ runId: string }> },
): Promise<Response> {
	const { runId } = await params;
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
	try {
		const detail = await client.getRunDetail(runId);
		return NextResponse.json(toDetail(detail));
	} catch (e) {
		if (e instanceof SwarmKitError) {
			return NextResponse.json(
				{ error: "runtime_error", detail: e.message },
				{ status: e.status === 404 ? 404 : 502 },
			);
		}
		throw e;
	}
}

function toDetail(a: AuditDetail): ActivityDetail {
	return {
		runId: a.run_id,
		topologyId: a.topology_id,
		startedAt: a.started_at,
		elapsedMs: a.elapsed_ms,
		status: a.status,
		summary: a.summary,
		archetypes: a.archetypes.map((x) => ({
			id: x.id,
			elapsedMs: x.elapsed_ms,
			status: x.status,
		})),
		errors: a.errors,
	};
}
