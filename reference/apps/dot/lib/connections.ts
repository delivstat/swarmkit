// Shape returned by GET /api/oauth/my-credentials on the runtime (per #982), projected into
// the DOT Connections page shape (design/details/dot-app.md §3.4).

export type Provider = "gmail" | "google-calendar";

export const PROVIDERS: { id: Provider; label: string }[] = [
	{ id: "gmail", label: "Gmail" },
	{ id: "google-calendar", label: "Google Calendar" },
];

export type ConnectionStatus =
	| { state: "connected"; owner: string; expiresAt: string }
	| { state: "expired"; owner: string }
	| { state: "not_connected" };

export interface SkillRef {
	id: string;
	name: string;
}

export interface ConnectionRow {
	provider: Provider;
	label: string;
	status: ConnectionStatus;
	/** Skills in the active workspace that need this credential — populated from the runtime's
	 * `used_by` field on `/api/oauth/my-credentials` (SwarmKit #1007). Drives the "Used by N
	 * skills" chip + the pre-disconnect warning that names the top skills about to break. */
	usedBy: SkillRef[];
}
