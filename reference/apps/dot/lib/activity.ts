// Activity page — a thin projection of the runtime's audit log scoped to DOT's own topologies
// (design/details/dot-app.md §3.6 Activity).

export type RunStatus = "success" | "error" | "running";

export interface ActivityEntry {
	runId: string;
	topologyId: string;
	startedAt: string;
	elapsedMs: number;
	status: RunStatus;
	summary: string;
}

export interface ActivityDetail extends ActivityEntry {
	archetypes: { id: string; elapsedMs: number; status: RunStatus }[];
	errors: { archetype: string; message: string }[];
}

export const DOT_TOPOLOGIES = ["morning-brief", "handle-item"] as const;
