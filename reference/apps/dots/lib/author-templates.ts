// Author Dot templates. Each lives at templates/*.yaml with a header manifest + a
// topology_template body — see templates/daily-brief.yaml for the format.
//
// We parse the YAML with a tiny hand-rolled extractor rather than pulling in a full YAML
// dependency: the format we accept is strict (keys at specific indent levels + a `|`
// block for topology_template), and this keeps the reference app dependency-free.
//
// Public surface:
//   loadTemplates()                    — read all template manifests from disk
//   substitute(topology_template, …)   — fill {{placeholders}}
//   writeTopologyYaml(id, yaml)        — write workspace/topologies/{id}.yaml

import { promises as fs } from "node:fs";
import path from "node:path";

export interface TemplateParam {
	key: string;
	prompt: string;
	default?: string;
}

export interface Template {
	id: string;
	name: string;
	summary: string;
	defaultIcon: string;
	defaultRole: string;
	defaultGreeting: string;
	renderers: string[];
	params: TemplateParam[];
	topologyTemplate: string;
}

const TEMPLATES_DIR = path.resolve(process.cwd(), "templates");
const WORKSPACE_TOPOLOGIES = path.resolve(process.cwd(), "workspace/topologies");

export async function loadTemplates(): Promise<Template[]> {
	let entries: string[];
	try {
		entries = await fs.readdir(TEMPLATES_DIR);
	} catch (err) {
		if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
		throw err;
	}
	const out: Template[] = [];
	for (const entry of entries) {
		if (!entry.endsWith(".yaml") && !entry.endsWith(".yml")) continue;
		const raw = await fs.readFile(path.join(TEMPLATES_DIR, entry), "utf8");
		const parsed = parseTemplate(raw);
		if (parsed) out.push(parsed);
	}
	return out;
}

export async function findTemplate(id: string): Promise<Template | undefined> {
	const all = await loadTemplates();
	return all.find((t) => t.id === id);
}

export function substitute(source: string, values: Record<string, string>): string {
	return source.replace(/\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}/g, (_m, key) => {
		return values[key] ?? "";
	});
}

export async function writeTopologyYaml(id: string, yaml: string): Promise<string> {
	await fs.mkdir(WORKSPACE_TOPOLOGIES, { recursive: true });
	const file = path.join(WORKSPACE_TOPOLOGIES, `${id}.yaml`);
	await fs.writeFile(file, yaml, "utf8");
	return file;
}

export async function topologyExists(id: string): Promise<boolean> {
	try {
		await fs.access(path.join(WORKSPACE_TOPOLOGIES, `${id}.yaml`));
		return true;
	} catch {
		return false;
	}
}

// ---- parser (strict) ------------------------------------------------------------------

interface RawTemplateHeader {
	template?: {
		id?: string;
		name?: string;
		summary?: string;
		default_icon?: string;
		default_role?: string;
		default_greeting?: string;
		renderers?: string[];
	};
	params?: Array<{ key?: string; prompt?: string; default?: string }>;
}

function parseTemplate(raw: string): Template | null {
	// Split "topology_template: |\n  …body…" from the header so we can YAML-parse the top.
	const idx = raw.indexOf("topology_template:");
	if (idx === -1) return null;
	const header = raw.slice(0, idx);
	const bodyRaw = raw.slice(idx);
	const bodyMatch = bodyRaw.match(/topology_template:\s*\|\s*\n([\s\S]*)$/);
	if (!bodyMatch?.[1]) return null;
	const topologyTemplate = dedentBlock(bodyMatch[1]);
	const parsed = parseFlatYaml(header) as RawTemplateHeader;
	if (!parsed.template?.id) return null;
	return {
		id: parsed.template.id,
		name: parsed.template.name ?? parsed.template.id,
		summary: parsed.template.summary ?? "",
		defaultIcon: parsed.template.default_icon ?? "sunrise",
		defaultRole: parsed.template.default_role ?? "",
		defaultGreeting: parsed.template.default_greeting ?? "",
		renderers: Array.isArray(parsed.template.renderers) ? parsed.template.renderers : [],
		params: (parsed.params ?? [])
			.filter(
				(p): p is { key: string; prompt?: string; default?: string } => typeof p.key === "string",
			)
			.map((p) => ({
				key: p.key,
				prompt: p.prompt ?? p.key,
				default: p.default,
			})),
		topologyTemplate,
	};
}

// Drops the common leading indentation from a `|` block so substitution is left-aligned.
function dedentBlock(block: string): string {
	const lines = block.split("\n");
	let min = Number.POSITIVE_INFINITY;
	for (const line of lines) {
		if (!line.trim()) continue;
		const m = line.match(/^ */);
		if (m) min = Math.min(min, m[0].length);
	}
	if (!Number.isFinite(min) || min === 0) return block;
	return lines
		.map((l) => (l.length >= min ? l.slice(min) : l))
		.join("\n")
		.trimEnd();
}

// A hand-rolled strict parser for the two-level flat shape we accept. Handles
// scalar strings (quoted/unquoted), one-line arrays [a, b, c], and nested blocks for
// `template:` + `params:`. Comments (`#`) and blank lines are skipped.
function parseFlatYaml(input: string): RawTemplateHeader {
	const out: RawTemplateHeader = {};
	const lines = input.split("\n");
	let section: "template" | "params" | null = null;
	let current: Record<string, unknown> | null = null;

	for (const raw of lines) {
		const line = raw.replace(/#.*$/, "").trimEnd();
		if (!line.trim()) continue;

		if (/^template:\s*$/.test(line)) {
			out.template = {};
			section = "template";
			current = null;
			continue;
		}
		if (/^params:\s*$/.test(line)) {
			out.params = [];
			section = "params";
			current = null;
			continue;
		}

		if (section === "template" && /^\s{2}([a-z_]+):\s*(.*)$/.test(line)) {
			const m = line.match(/^\s{2}([a-z_]+):\s*(.*)$/);
			if (!m) continue;
			const [, key, value] = m;
			if (!key) continue;
			(out.template as Record<string, unknown>)[key] = parseScalar(value ?? "");
			continue;
		}

		if (section === "params") {
			if (/^\s{2}-\s/.test(line)) {
				current = {};
				out.params?.push(current as { key?: string; prompt?: string; default?: string });
				const m = line.match(/^\s{2}-\s+([a-z_]+):\s*(.*)$/);
				if (m?.[1]) current[m[1]] = parseScalar(m[2] ?? "");
				continue;
			}
			if (current && /^\s{4}([a-z_]+):\s*(.*)$/.test(line)) {
				const m = line.match(/^\s{4}([a-z_]+):\s*(.*)$/);
				if (m?.[1]) current[m[1]] = parseScalar(m[2] ?? "");
			}
		}
	}
	return out;
}

function parseScalar(value: string): string | string[] {
	const trimmed = value.trim();
	if (!trimmed) return "";
	// One-line array: [a, b, c]
	if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
		return trimmed
			.slice(1, -1)
			.split(",")
			.map((s) => s.trim())
			.filter(Boolean);
	}
	// Quoted string
	if (
		(trimmed.startsWith("'") && trimmed.endsWith("'")) ||
		(trimmed.startsWith('"') && trimmed.endsWith('"'))
	) {
		return trimmed.slice(1, -1);
	}
	return trimmed;
}
