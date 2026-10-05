import { loadTemplates } from "@/lib/author-templates";
import { type NextRequest, NextResponse } from "next/server";

// GET /api/author-templates — list the Author Dot's templates so the wizard client can
// render a picker + question flow without duplicating the manifest shape.

export const runtime = "nodejs";

export async function GET(_req: NextRequest): Promise<Response> {
	const templates = await loadTemplates();
	return NextResponse.json({
		templates: templates.map((t) => ({
			id: t.id,
			name: t.name,
			summary: t.summary,
			params: t.params,
		})),
	});
}
