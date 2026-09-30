// Shared TS types for the DOT app. Kept flat and grow-as-needed. The wire shape is set by the
// morning-brief topology's output_schema (reference/workspaces/dot/topologies/morning-brief.yaml).

export type Source = "gmail" | "calendar";

export type SuggestedAction =
	| "reply"
	| "draft_reply"
	| "archive"
	| "attend"
	| "decline"
	| "prep"
	| "retrieve_context";

export interface Item {
	id: string;
	source: Source;
	title: string;
	why: string;
	rank: number;
	suggestedAction: SuggestedAction;
	peerActions: SuggestedAction[];
	sourceRef?: string;
	receivedAt?: string;
	scheduledFor?: string;
}

export interface Brief {
	runId: string;
	generatedAt: string;
	items: Item[];
}

export interface HistoryEntry {
	runId: string;
	generatedAt: string;
	itemCount: number;
}
