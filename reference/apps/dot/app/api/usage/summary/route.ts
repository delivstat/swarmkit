import { COOKIE_NAME, verifySession } from "@/lib/session";
import { SwarmKitError, clientFromEnv } from "@/lib/swarmkit-client";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export async function GET(): Promise<Response> {
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
		const summary = await client.getUsageSummary();
		return NextResponse.json(summary);
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
