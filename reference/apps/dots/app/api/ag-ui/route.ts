import { findDot } from "@/lib/dots.config";
import { type NextRequest, NextResponse } from "next/server";

// Proxy POST /api/ag-ui?dotId=<id> → SwarmKit's POST /api/ag-ui/run.
//
// The body is an AG-UI `RunAgentInput` (threadId, runId, messages, tools, context, state) sent
// by @ag-ui/client's HttpAgent. We rewrite `context.topology` from the Dot config before
// forwarding; everything else is passed through verbatim so the SSE stream SwarmKit emits
// reaches the browser untouched.
//
// Why query param, not body: `HttpAgent` composes the body itself; the Dot id has to ride on
// the URL. The server adds the topology server-side so the browser never names a topology that
// has not been declared in dots.config.ts.
//
// SWARMKIT_URL defaults to http://127.0.0.1:8000 (uvicorn default). The standalone demo points
// it at the mock runtime instead.

export const runtime = "nodejs";

interface AgUiRunInput {
	threadId?: string;
	runId?: string;
	messages?: unknown[];
	tools?: unknown[];
	context?: Record<string, unknown>;
	state?: Record<string, unknown>;
	forwardedProps?: Record<string, unknown>;
}

export async function POST(req: NextRequest): Promise<Response> {
	const dotId = req.nextUrl.searchParams.get("dotId");
	const dot = dotId ? await findDot(dotId) : undefined;
	if (!dot) return NextResponse.json({ error: "dot_not_found" }, { status: 404 });

	let body: AgUiRunInput;
	try {
		body = (await req.json()) as AgUiRunInput;
	} catch {
		return NextResponse.json({ error: "invalid_json" }, { status: 400 });
	}

	const forwarded = {
		...body,
		context: { ...(body.context ?? {}), topology: dot.topology },
	};

	const base = process.env.SWARMKIT_URL ?? "http://127.0.0.1:8000";
	const upstream = await fetch(`${base}/api/ag-ui/run`, {
		method: "POST",
		headers: { "content-type": "application/json", accept: "text/event-stream" },
		body: JSON.stringify(forwarded),
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
