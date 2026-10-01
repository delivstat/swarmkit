import { COOKIE_NAME, verifySession } from "@/lib/session";
import { SwarmKitError, clientFromEnv } from "@/lib/swarmkit-client";
import { cookies, headers } from "next/headers";
import { NextResponse } from "next/server";

// The browser POSTs here, we call the runtime's /oauth/{provider}/start with a return_to that
// points back to /connections?connected=<provider>, and hand the Google auth URL back to the
// client. The client then window.location = <url> so the top-level navigation lands on Google's
// consent screen. After Google → runtime callback → runtime issues 302 to our return_to.
export async function POST(
	_req: Request,
	{ params }: { params: Promise<{ provider: string }> },
): Promise<Response> {
	const { provider } = await params;
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
	const h = await headers();
	const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3400";
	const proto = h.get("x-forwarded-proto") ?? "http";
	const returnTo = `${proto}://${host}/connections?connected=${encodeURIComponent(provider)}`;
	try {
		const authUrl = await client.startOAuth(provider, returnTo);
		return NextResponse.json({ auth_url: authUrl });
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
