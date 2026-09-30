import { COOKIE_NAME, verifySession } from "@/lib/session";
import {
	type Settings,
	type WorkspaceConfigProjection,
	toPatch,
	toSettings,
} from "@/lib/settings";
import { SwarmKitError, clientFromEnv } from "@/lib/swarmkit-client";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export async function GET(): Promise<Response> {
	const session = await requireOwner();
	if (session instanceof Response) return session;
	const client = clientFromEnv(session.owner);
	if (!client) {
		return NextResponse.json(
			{ error: "runtime not configured" },
			{ status: 503 },
		);
	}
	try {
		const cfg = await client.getWorkspaceConfig<WorkspaceConfigProjection>();
		return NextResponse.json({ settings: toSettings(cfg) });
	} catch (e) {
		return runtimeError(e);
	}
}

export async function PATCH(req: Request): Promise<Response> {
	const session = await requireOwner();
	if (session instanceof Response) return session;
	const client = clientFromEnv(session.owner);
	if (!client) {
		return NextResponse.json(
			{ error: "runtime not configured" },
			{ status: 503 },
		);
	}
	let partial: Partial<Settings>;
	try {
		partial = (await req.json()) as Partial<Settings>;
	} catch {
		return NextResponse.json({ error: "invalid_json" }, { status: 400 });
	}
	try {
		const cfg = await client.patchWorkspaceConfig<WorkspaceConfigProjection>(
			toPatch(partial),
		);
		return NextResponse.json({ settings: toSettings(cfg) });
	} catch (e) {
		return runtimeError(e);
	}
}

async function requireOwner(): Promise<{ owner: string } | Response> {
	const store = await cookies();
	const payload = await verifySession(store.get(COOKIE_NAME)?.value);
	if (!payload)
		return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
	return { owner: payload.owner };
}

function runtimeError(e: unknown): Response {
	if (e instanceof SwarmKitError) {
		return NextResponse.json(
			{ error: "runtime_error", detail: e.message },
			{ status: 502 },
		);
	}
	throw e;
}
