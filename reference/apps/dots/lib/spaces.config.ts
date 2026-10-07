// A Space is a persistent, scoped surface that holds the state of an ongoing
// collaboration between the user and one or more Dots. See
// design/details/spaces-pattern.md.
//
// Shape: a user-chosen id ("launch-q4-campaign") becomes the correlation_id on every
// AG-UI run started inside the Space. Activity, artifacts, approvals and the
// governed-memory namespace all key off that id — no runtime addition.
//
// Storage: identical layout to dots.config.ts. Built-in Spaces are rare (the auto-
// created daily brief is Dot-driven, not shipped-at-boot); the local file is the
// primary store. Local Spaces win on id conflict.

import { promises as fs } from "node:fs";
import path from "node:path";

export interface Space {
	id: string;
	name: string;
	description: string;
	owner: string;
	created_at: string;
}

// Lower-kebab id; also the correlation_id prefix on AG-UI runs ("space:<id>").
export const SPACE_ID_PATTERN = /^[a-z][a-z0-9-]*$/;

// correlation_id convention. The Space view's filter uses this verbatim.
export function correlationIdFor(spaceId: string): string {
	return `space:${spaceId}`;
}

// No shipped built-in Spaces for now; the primitive is user-driven. Auto-created
// Spaces (e.g. daily brief:<date>) live in local storage like any other.
export const BUILT_IN_SPACES: Space[] = [];

export const LOCAL_SPACES_PATH = path.resolve(process.cwd(), "lib/spaces.local.json");

export async function readLocalSpaces(): Promise<Space[]> {
	try {
		const raw = await fs.readFile(LOCAL_SPACES_PATH, "utf8");
		const parsed = JSON.parse(raw) as unknown;
		if (!Array.isArray(parsed)) return [];
		return parsed.filter(isSpace);
	} catch (err) {
		if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
		throw err;
	}
}

export async function writeLocalSpaces(spaces: Space[]): Promise<void> {
	await fs.writeFile(LOCAL_SPACES_PATH, `${JSON.stringify(spaces, null, 2)}\n`, "utf8");
}

export async function loadSpaces(): Promise<Space[]> {
	const local = await readLocalSpaces();
	const localIds = new Set(local.map((s) => s.id));
	return [...local, ...BUILT_IN_SPACES.filter((s) => !localIds.has(s.id))];
}

export async function findSpace(id: string): Promise<Space | undefined> {
	const spaces = await loadSpaces();
	return spaces.find((s) => s.id === id);
}

function isSpace(value: unknown): value is Space {
	if (!value || typeof value !== "object") return false;
	const v = value as Record<string, unknown>;
	return (
		typeof v.id === "string" &&
		typeof v.name === "string" &&
		typeof v.description === "string" &&
		typeof v.owner === "string" &&
		typeof v.created_at === "string"
	);
}
