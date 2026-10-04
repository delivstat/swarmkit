// A Dot is a chat surface: one role, one topology on the SwarmKit side, a set of renderers
// for structured payloads it emits inline. This is a frontend grouping — SwarmKit's runtime
// does not know what a Dot is; it only sees `context.topology` on each AG-UI run.
//
// If #1014 (shared `kind: Dot` schema) lands upstream, this file migrates to `dots/*.yaml`
// files under this app, loaded through the shared validator. Until then this config.ts is
// the single source of truth for the app.

export interface Dot {
	id: string;
	name: string;
	role: string;
	icon: string;
	topology: string;
	greeting: string;
	renderers: string[];
}

export const DOTS: Dot[] = [
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

export function findDot(id: string): Dot | undefined {
	return DOTS.find((d) => d.id === id);
}
