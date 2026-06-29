/** Mirrors `Instance.public_dict()` from the swarmkit-control-plane API. */

export type ConnectionMode = "direct" | "poll";
export type Health = "healthy" | "stale" | "unreachable" | "unknown";
export type CommandStatus = "queued" | "dispatched" | "done" | "error";

export interface Instance {
	id: string;
	name: string;
	endpoint: string;
	connection: ConnectionMode;
	tier: string;
	token_fingerprint: string;
	token_minted_at: string | null;
	schema_version: string;
	capabilities: Record<string, unknown>;
	health: Health;
	last_seen: string | null;
	created_at: string;
}

/** Mirrors `Command.public_dict()`. */
export interface Command {
	cmd_id: string;
	instance_id: string;
	verb: string;
	args: Record<string, unknown>;
	status: CommandStatus;
	output: Record<string, unknown> | null;
	error: string | null;
	created_at: string;
	dispatched_at: string | null;
	result_at: string | null;
}

/** Response of POST /instances/{id}/mint-token — the token is shown once. */
export interface MintResult {
	token: string;
	client_id: string;
	client_name: string;
	tier: string;
	key_ref: string;
	fingerprint: string;
	server_auth_snippet: string;
	instructions: string;
}

/** Row of GET /usage — token/cost totals per model+provider across the fleet. */
export interface UsageRow {
	model: string | null;
	provider: string | null;
	input_tokens: number;
	output_tokens: number;
	cost_usd: number;
	records: number;
}

/** Row of GET /eval — pass-rate per eval_set+topology across the fleet. */
export interface EvalRow {
	eval_set: string | null;
	topology: string | null;
	passed: number;
	total: number;
	runs: number;
	pass_rate: number | null;
}

/** Row of GET /audit — a recent fleet event, tagged with its instance. */
export interface AuditRow {
	instance_id: string;
	ts?: string;
	action?: string;
	[key: string]: unknown;
}

/** Row of GET /artifacts — one per (kind, id) with its latest version. */
export interface ArtifactSummary {
	kind: string;
	id: string;
	versions: number;
	latest_version: string;
	latest_hash: string;
}

/** A registered artifact version (GET /artifacts/{kind}/{id}/versions[/{version}]). */
export interface ArtifactVersion {
	kind: string;
	id: string;
	version: string;
	content_hash: string;
	authored_by: string;
	schema_version: string;
	created_at: string;
	content?: unknown;
}

/** Configured observability endpoints (GET /observability). Empty strings when unconfigured. */
export interface Observability {
	collector_endpoint: string;
	jaeger_url: string;
	grafana_url: string;
}

/** Command verbs the panel may enqueue, with the tier each requires (mirrors _verbs.py). */
export const KNOWN_VERBS: { verb: string; tier: string }[] = [
	{ verb: "capabilities", tier: "read" },
	{ verb: "usage", tier: "read" },
	{ verb: "job-status", tier: "read" },
	{ verb: "validate", tier: "read" },
	{ verb: "run", tier: "run" },
	{ verb: "reload", tier: "admin" },
];
