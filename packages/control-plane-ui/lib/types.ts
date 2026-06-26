/** Mirrors `Instance.public_dict()` from the swarmkit-control-plane API. */

export type ConnectionMode = "direct" | "poll";
export type Health = "healthy" | "stale" | "unreachable" | "unknown";

export interface Instance {
	id: string;
	name: string;
	endpoint: string;
	connection: ConnectionMode;
	schema_version: string;
	capabilities: Record<string, unknown>;
	health: Health;
	last_seen: string | null;
	created_at: string;
}
