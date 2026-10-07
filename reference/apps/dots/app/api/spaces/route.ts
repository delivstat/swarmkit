import { type NextRequest, NextResponse } from "next/server";

import {
	SPACE_ID_PATTERN,
	type Space,
	loadSpaces,
	readLocalSpaces,
	writeLocalSpaces,
} from "@/lib/spaces.config";

// GET /api/spaces — list Spaces (shipped + local merged), newest-first by created_at.
// POST /api/spaces — create a new Space; stored in spaces.local.json.
//
// A Space is a user-chosen scope that gets threaded as correlation_id on every AG-UI
// run inside it. See design/details/spaces-pattern.md.

export const runtime = "nodejs";

export async function GET(): Promise<Response> {
	const spaces = (await loadSpaces()).sort((a, b) => b.created_at.localeCompare(a.created_at));
	return NextResponse.json({ spaces });
}

interface CreateBody {
	id?: string;
	name?: string;
	description?: string;
	owner?: string;
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
	const description = (body.description ?? "").trim();
	const owner = (body.owner ?? "owner").trim();

	if (!SPACE_ID_PATTERN.test(id)) {
		return NextResponse.json(
			{
				error: "invalid_id",
				detail: "id must start with a lowercase letter and contain only [a-z0-9-]",
			},
			{ status: 400 },
		);
	}
	if (!name) {
		return NextResponse.json(
			{ error: "missing_field", detail: "name is required" },
			{ status: 400 },
		);
	}

	const existing = await loadSpaces();
	if (existing.some((s) => s.id === id)) {
		return NextResponse.json(
			{ error: "id_in_use", detail: `A Space with id '${id}' already exists.` },
			{ status: 409 },
		);
	}

	const space: Space = {
		id,
		name,
		description,
		owner,
		created_at: new Date().toISOString(),
	};
	const local = await readLocalSpaces();
	await writeLocalSpaces([space, ...local]);
	return NextResponse.json({ ok: true, space }, { status: 201 });
}
