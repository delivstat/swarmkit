import {
	DOT_ID_PATTERN,
	type Dot,
	loadDots,
	readLocalDots,
	writeLocalDots,
} from "@/lib/dots.config";
import { type NextRequest, NextResponse } from "next/server";

// POST /api/dots — add a user-created Dot to dots.local.json. The payload shape mirrors the
// Dot interface; renderers defaults to [] when the client sends nothing.
//
// The form is the only writer: validation lives here (not in the client) so a direct curl
// against this endpoint can't poison the local store.

export const runtime = "nodejs";

interface CreateBody {
	id?: string;
	name?: string;
	role?: string;
	icon?: string;
	topology?: string;
	greeting?: string;
	renderers?: unknown;
}

export async function POST(req: NextRequest): Promise<Response> {
	let body: CreateBody;
	try {
		body = (await req.json()) as CreateBody;
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
	if (existing.some((d) => d.id === id)) {
		return NextResponse.json(
			{ error: "id_in_use", detail: `A Dot with id '${id}' already exists.` },
			{ status: 409 },
		);
	}

	const dot: Dot = { id, name, role, icon, topology, greeting, renderers };
	const local = await readLocalDots();
	await writeLocalDots([...local, dot]);
	return NextResponse.json({ ok: true, dot }, { status: 201 });
}
