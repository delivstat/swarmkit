import { COOKIE_NAME, verifySession } from "@/lib/session";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

// Connection status lands in commit 6 once /api/connections/* is wired to the runtime.
export async function GET(): Promise<Response> {
	const store = await cookies();
	const payload = await verifySession(store.get(COOKIE_NAME)?.value);
	if (!payload) {
		return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
	}
	return NextResponse.json({
		owner: payload.owner,
		connected: { gmail: false, calendar: false },
	});
}
