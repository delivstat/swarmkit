import {
	type ConnectionRow,
	type ConnectionStatus,
	PROVIDERS,
} from "@/lib/connections";
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
		const creds = await client.getMyCredentials();
		const rows: ConnectionRow[] = PROVIDERS.map(({ id, label }) => {
			const cred = creds.find((c) => c.provider === id);
			return { provider: id, label, status: toStatus(cred, payload.owner) };
		});
		return NextResponse.json({ rows });
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

function toStatus(
	cred: { expires_at: string | null; expired: boolean } | undefined,
	owner: string,
): ConnectionStatus {
	if (!cred) return { state: "not_connected" };
	if (cred.expired) return { state: "expired", owner };
	return {
		state: "connected",
		owner,
		expiresAt: cred.expires_at ?? "",
	};
}
