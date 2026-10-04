import { findDot } from "@/lib/dots.config";
import { type NextRequest, NextResponse } from "next/server";

// Proxy POST /api/ag-ui → SwarmKit's POST /api/ag-ui/run.
//
// We terminate auth here (middleware already verified the session cookie), then forward the
// user turn as an AG-UI RunAgentInput with context.topology set from the Dot config. The SSE
// response is piped straight through to the client.
//
// SWARMKIT_URL defaults to http://127.0.0.1:8000 (uvicorn default). In the standalone demo it
// points at the mock runtime instead.

export const runtime = "nodejs";

interface RunBody {
	dotId: string;
	message: string;
}

export async function POST(req: NextRequest): Promise<Response> {
	let body: RunBody;
	try {
		body = (await req.json()) as RunBody;
	} catch {
		return NextResponse.json({ error: "invalid_json" }, { status: 400 });
	}
	const dot = findDot(body.dotId);
	if (!dot) return NextResponse.json({ error: "dot_not_found" }, { status: 404 });

	const base = process.env.SWARMKIT_URL ?? "http://127.0.0.1:8000";
	const upstream = await fetch(`${base}/api/ag-ui/run`, {
		method: "POST",
		headers: { "content-type": "application/json", accept: "text/event-stream" },
		body: JSON.stringify({
			threadId: `${dot.id}:default`,
			messages: [{ role: "user", content: body.message }],
			context: { topology: dot.topology },
		}),
	});
	if (!upstream.ok || !upstream.body) {
		return NextResponse.json({ error: "upstream_error", status: upstream.status }, { status: 502 });
	}
	return new Response(upstream.body, {
		headers: {
			"content-type": "text/event-stream",
			"cache-control": "no-cache, no-transform",
			connection: "keep-alive",
		},
	});
}
