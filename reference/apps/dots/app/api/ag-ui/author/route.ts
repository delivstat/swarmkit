import { type NextRequest, NextResponse } from "next/server";

// Proxy POST /api/ag-ui/author → SwarmKit's POST /api/ag-ui/run with the Dot Author topology.
//
// The Dot Author is the one topology the dots app ships for creating new Dots. It elicits the
// requirement in chat, delegates topology generation to the bundled `swarmkit:author:topology`
// via its `author-topology` skill (#1055), and calls `create-dot` to persist the Dot. See
// reference/workspaces/author/.
//
// Shape mirrors ../route.ts (per-Dot proxy) with topology pinned to "author" instead of being
// read from a Dot id query param — authoring is the one non-Dot chat surface on this app.
//
// Env:
//   SWARMKIT_URL — upstream serve. Defaults to http://127.0.0.1:8000.

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
	let body: AgUiRunInput;
	try {
		body = (await req.json()) as AgUiRunInput;
	} catch {
		return NextResponse.json({ error: "invalid_json" }, { status: 400 });
	}

	const forwarded = {
		...body,
		context: { ...(body.context ?? {}), topology: "author" },
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
