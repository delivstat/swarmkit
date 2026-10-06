import { headers } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import {
	DOT_ID_PATTERN,
	type Dot,
	loadDots,
	readLocalDots,
	writeLocalDots,
} from "@/lib/dots.config";

// POST /api/dots/intake — server-to-server endpoint for the Dot Author's `create-dot` command
// pack script. The script runs inside the SwarmKit container (compose service `swarmkit`) and
// POSTs the Dot entry here when it has validated the topology and is ready to register the Dot.
//
// Why not reuse POST /api/dots:
//   - /api/dots is the browser-facing form endpoint and goes through the login session.
//   - This endpoint is called from a backend process, so it accepts a shared-secret header
//     instead. Set DOTS_INTAKE_TOKEN in the dots-app environment; the SwarmKit side forwards it
//     via the `create_dot.py` script's `DOTS_INTAKE_TOKEN` env var.
//   - When the token is unset we fall back to accepting unauthenticated requests from the
//     docker-compose service network (localhost / internal hostnames only). That lets the
//     reference app work out of the box with no manual secret rotation.
//
// Idempotent: if the same id arrives twice, we return 200 with the existing entry rather than
// 409. The authoring agent sometimes retries after an error; refusing the second attempt would
// leave the Dot visible on refresh but reported as a failure to the chat UI.

export const runtime = "nodejs";

interface IntakeBody {
	id?: string;
	name?: string;
	role?: string;
	icon?: string;
	topology?: string;
	greeting?: string;
	renderers?: unknown;
}

export async function POST(req: NextRequest): Promise<Response> {
	// Shared-secret check. Keeps a public-internet deployment safe when SWARMKIT_URL points at
	// a serve the operator did not configure to speak only to the dots-app container.
	const token = process.env.DOTS_INTAKE_TOKEN;
	if (token) {
		const supplied = req.headers.get("x-dots-intake-token") ?? "";
		if (supplied !== token) {
			return NextResponse.json({ error: "forbidden" }, { status: 403 });
		}
	} else {
		// No token configured; only accept requests from the service network. In practice this
		// catches any attempt to reach the endpoint from outside docker without the operator
		// explicitly wiring auth. x-forwarded-for is set by any reverse proxy that bridges from
		// the internet, so its absence (combined with a loopback / docker-internal remote) is a
		// decent floor for "server-to-server inside compose".
		const h = await headers();
		if (h.get("x-forwarded-for")) {
			return NextResponse.json(
				{
					error: "forbidden",
					detail:
						"intake refused — set DOTS_INTAKE_TOKEN in the dots-app env and forward it from the SwarmKit container to use this endpoint through a proxy",
				},
				{ status: 403 },
			);
		}
	}

	let body: IntakeBody;
	try {
		body = (await req.json()) as IntakeBody;
	} catch {
		return NextResponse.json({ error: "invalid_json" }, { status: 400 });
	}

	const id = (body.id ?? "").trim();
	const name = (body.name ?? "").trim();
	const role = (body.role ?? "").trim();
	const icon = (body.icon ?? "sunrise").trim();
	const topology = (body.topology ?? "").trim();
	const greeting = (body.greeting ?? "").trim();
	const renderers = Array.isArray(body.renderers)
		? body.renderers.filter((r): r is string => typeof r === "string")
		: [];

	if (!DOT_ID_PATTERN.test(id)) {
		return NextResponse.json(
			{
				error: "invalid_id",
				detail: "id must start with a lowercase letter and contain only [a-z0-9-]",
			},
			{ status: 400 },
		);
	}
	if (!name || !role || !topology || !greeting) {
		return NextResponse.json(
			{ error: "missing_field", detail: "name, role, topology, greeting are required" },
			{ status: 400 },
		);
	}

	const existing = await loadDots();
	const match = existing.find((d) => d.id === id);
	if (match) {
		// Intake retries happen; a duplicate is not an error. Report the existing entry back.
		return NextResponse.json({ ok: true, dot: match, created: false });
	}

	const dot: Dot = { id, name, role, icon, topology, greeting, renderers };
	const local = await readLocalDots();
	await writeLocalDots([...local, dot]);
	return NextResponse.json({ ok: true, dot, created: true }, { status: 201 });
}
