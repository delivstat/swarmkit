import { startHandleRun } from "@/lib/runtime-run";
import { COOKIE_NAME, verifySession } from "@/lib/session";
import { encodeEvent } from "@/lib/sse";
import { SwarmKitError, clientFromEnv } from "@/lib/swarmkit-client";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

interface ChatBody {
	userIntent?: string;
}

export async function POST(
	req: Request,
	{ params }: { params: Promise<{ id: string }> },
): Promise<Response> {
	const { id } = await params;
	const store = await cookies();
	const payload = await verifySession(store.get(COOKIE_NAME)?.value);
	if (!payload) {
		return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
	}
	let body: ChatBody;
	try {
		body = (await req.json()) as ChatBody;
	} catch {
		return NextResponse.json({ error: "invalid_json" }, { status: 400 });
	}
	if (!body.userIntent?.trim()) {
		return NextResponse.json(
			{ error: "user_intent_required" },
			{ status: 400 },
		);
	}
	const client = clientFromEnv(payload.owner);
	if (!client) {
		return NextResponse.json(
			{ error: "runtime not configured" },
			{ status: 503 },
		);
	}
	try {
		const upstream = await startHandleRun(client, {
			itemId: id,
			suggestedAction: "chat",
			userIntent: body.userIntent,
			topology: "handle-item",
		});
		if (!upstream.body) return sseError("empty upstream response");
		return new Response(upstream.body, {
			status: 200,
			headers: {
				"Content-Type": "text/event-stream",
				"Cache-Control": "no-cache, no-transform",
				Connection: "keep-alive",
			},
		});
	} catch (e) {
		if (e instanceof SwarmKitError) return sseError(e.message);
		throw e;
	}
}

function sseError(detail: string): Response {
	const stream = new ReadableStream({
		start(controller) {
			controller.enqueue(encodeEvent({ type: "error", detail }));
			controller.close();
		},
	});
	return new Response(stream, {
		status: 200,
		headers: {
			"Content-Type": "text/event-stream",
			"Cache-Control": "no-cache",
		},
	});
}
