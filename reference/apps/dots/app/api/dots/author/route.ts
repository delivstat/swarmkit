import {
	findTemplate,
	substitute,
	topologyExists,
	writeTopologyYaml,
} from "@/lib/author-templates";
import {
	DOT_ID_PATTERN,
	type Dot,
	loadDots,
	readLocalDots,
	writeLocalDots,
} from "@/lib/dots.config";
import { type NextRequest, NextResponse } from "next/server";

// POST /api/dots/author — Author Dot wizard submission. Fills a template with the user's
// answers, writes the topology YAML to workspace/topologies/, and writes a matching Dot
// entry. Returns the Dot so the client can redirect to its chat.
//
// Idempotent on refusal: nothing is written if validation fails. Non-idempotent on success —
// a repeat with the same id gets 409. The browser's wizard commits exactly once.

export const runtime = "nodejs";

interface AuthorBody {
	templateId?: string;
	dotId?: string;
	dotName?: string;
	params?: Record<string, string>;
}

export async function POST(req: NextRequest): Promise<Response> {
	let body: AuthorBody;
	try {
		body = (await req.json()) as AuthorBody;
	} catch {
		return NextResponse.json({ error: "invalid_json" }, { status: 400 });
	}

	const templateId = (body.templateId ?? "").trim();
	const dotId = (body.dotId ?? "").trim();
	const dotName = (body.dotName ?? "").trim();
	const params = body.params ?? {};

	if (!templateId || !dotId || !dotName) {
		return NextResponse.json(
			{ error: "missing_field", detail: "templateId, dotId, dotName are required" },
			{ status: 400 },
		);
	}
	if (!DOT_ID_PATTERN.test(dotId)) {
		return NextResponse.json(
			{ error: "invalid_id", detail: "dotId must match ^[a-z][a-z0-9-]*$" },
			{ status: 400 },
		);
	}

	const template = await findTemplate(templateId);
	if (!template) {
		return NextResponse.json({ error: "unknown_template", detail: templateId }, { status: 404 });
	}

	const existingDots = await loadDots();
	if (existingDots.some((d) => d.id === dotId)) {
		return NextResponse.json(
			{ error: "id_in_use", detail: `A Dot with id '${dotId}' already exists.` },
			{ status: 409 },
		);
	}
	if (await topologyExists(dotId)) {
		return NextResponse.json(
			{
				error: "topology_exists",
				detail: `workspace/topologies/${dotId}.yaml already exists.`,
			},
			{ status: 409 },
		);
	}

	const yaml = substitute(template.topologyTemplate, {
		dot_id: dotId,
		dot_name: dotName,
		...params,
	});
	await writeTopologyYaml(dotId, yaml);

	const dot: Dot = {
		id: dotId,
		name: dotName,
		role: template.defaultRole,
		icon: template.defaultIcon,
		topology: dotId,
		greeting: template.defaultGreeting,
		renderers: template.renderers,
	};
	const localDots = await readLocalDots();
	await writeLocalDots([...localDots, dot]);
	return NextResponse.json({ ok: true, dot }, { status: 201 });
}
