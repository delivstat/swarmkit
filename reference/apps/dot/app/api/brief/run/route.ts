import { fetchTodaysBrief } from "@/lib/brief";
import { COOKIE_NAME, verifySession } from "@/lib/session";
import { SwarmKitError, clientFromEnv } from "@/lib/swarmkit-client";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

// Ad-hoc refresh: rerun the fast lane and return the fresh brief. Commit 5 will grow the
// streaming variant for per-item handling; the fast-lane brief itself has nothing to stream.
export async function POST(): Promise<Response> {
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
		const brief = await fetchTodaysBrief(client);
		return NextResponse.json(brief);
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
