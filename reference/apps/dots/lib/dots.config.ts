// A Dot is a chat surface: one role, one topology on the SwarmKit side, a set of renderers
// for structured payloads it emits inline. This is a frontend grouping — SwarmKit's runtime
// does not know what a Dot is; it only sees `context.topology` on each AG-UI run.
//
// Dots come from two places and merge at request time: the built-in `BUILT_IN_DOTS` list
// below (ships with the app) and `dots.local.json` next to this file (user-created via the
// in-app /dots/new form). Local Dots win on id conflict so a user override replaces a shipped
// Dot rather than hiding behind it.
//
// If #1014 (shared `kind: Dot` schema) lands upstream, this file migrates to `dots/*.yaml`
// files loaded through the shared validator. Until then this is the single source of truth.

import { promises as fs } from "node:fs";
import path from "node:path";

export interface Dot {
	id: string;
	name: string;
	role: string;
	icon: string;
	topology: string;
	greeting: string;
	renderers: string[];
}

export const BUILT_IN_DOTS: Dot[] = [
	{
		id: "morning-brief",
		name: "Morning Brief",
		role: "Daily brief curator",
		icon: "sunrise",
		topology: "morning-brief",
		greeting: "Ready for today's brief. Shall I pull it?",
		renderers: ["brief-item"],
	},
	{
		id: "handle-item",
		name: "Handle Item",
		role: "Delegate for a specific brief item",
		icon: "mail-reply",
		topology: "handle-item",
		greeting: "Tell me which item from your brief you want to handle.",
		renderers: ["email-draft", "prep-note"],
	},
	{
		id: "github-triage",
		name: "GitHub Triage",
		role: "Issue + PR triage coworker",
		icon: "github",
		topology: "github-triage",
		greeting: "Ask me about an issue or PR, or say 'triage inbox'.",
		renderers: ["github-item"],
	},
];

export const LOCAL_DOTS_PATH = path.resolve(process.cwd(), "lib/dots.local.json");

export const DOT_ID_PATTERN = /^[a-z][a-z0-9-]*$/;

export async function readLocalDots(): Promise<Dot[]> {
	try {
		const raw = await fs.readFile(LOCAL_DOTS_PATH, "utf8");
		const parsed = JSON.parse(raw) as unknown;
		if (!Array.isArray(parsed)) return [];
		return parsed.filter(isDot);
	} catch (err) {
		if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
		throw err;
	}
}

export async function writeLocalDots(dots: Dot[]): Promise<void> {
	await fs.writeFile(LOCAL_DOTS_PATH, `${JSON.stringify(dots, null, 2)}\n`, "utf8");
}

export async function loadDots(): Promise<Dot[]> {
	const local = await readLocalDots();
	const localIds = new Set(local.map((d) => d.id));
	// Local Dots win on id conflict.
	return [...local, ...BUILT_IN_DOTS.filter((d) => !localIds.has(d.id))];
}

export async function findDot(id: string): Promise<Dot | undefined> {
	const dots = await loadDots();
	return dots.find((d) => d.id === id);
}

function isDot(value: unknown): value is Dot {
	if (!value || typeof value !== "object") return false;
	const v = value as Record<string, unknown>;
	return (
		typeof v.id === "string" &&
		typeof v.name === "string" &&
		typeof v.role === "string" &&
		typeof v.icon === "string" &&
		typeof v.topology === "string" &&
		typeof v.greeting === "string" &&
		Array.isArray(v.renderers)
	);
}
