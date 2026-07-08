/** Mirrors `Instance.public_dict()` from the swarmkit-control-plane API. */

// KNOWN_VERBS is generated from the panel's canonical VERB_TIERS — re-exported here so consumers
// keep importing verbs from `@/lib/types`. Regenerate with `just codegen-verbs`.
export { KNOWN_VERBS, type KnownVerb, type Tier } from "./generated/verbs";

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

/** Read-only panel config (GET /config) — flags + URLs, never secrets. */
export interface Config {
	version: string;
	auth: {
		operator_tokens: boolean;
		oidc: { enabled: boolean; issuer: string; audience: string };
	};
	cors_origins: string[];
	observability: {
		collector_endpoint: string;
		jaeger_url: string;
		grafana_url: string;
	};
}

/** A drafted artifact the authoring swarm emitted (POST /instances/{id}/author). */
export interface DraftArtifact {
	kind: string;
	id: string;
	content: unknown;
}

/** Response of POST /instances/{id}/author — the swarm's reply + any drafted artifact. */
export interface AuthorResult {
	reply: string;
	artifact: DraftArtifact | null;
}

/** Row of GET /gaps — a skill gap ranked across the fleet (signal → surface). */
export interface Gap {
	capability: string;
	occurrences: number;
	instances: number;
	last_seen: string | null;
	description: string | null;
}

export type ProposalStatus = "pending" | "approved" | "rejected";

/** A growth-loop proposal (GET /proposals) — a drafted artifact change awaiting human approval. */
export interface Proposal {
	id: string;
	kind: string;
	artifact_id: string;
	content: unknown;
	status: ProposalStatus;
	proposed_by: string;
	signal: string;
	eval_summary: Record<string, unknown>;
	approved_by: string;
	reason: string;
	published_version: string;
	created_at: string;
	decided_at: string | null;
}

/** A live job from an instance's serve /jobs (federated via GET /instances/{id}/jobs). */
export interface Job {
	job_id: string;
	topology: string;
	version?: string;
	status: string;
	created_at: string;
	completed_at?: string | null;
}

export type DriftStatus = "ok" | "drift" | "missing";

/** Row of GET /instances/{id}/drift — registry-intended vs the instance's reported actual. */
export interface DriftRow {
	kind: string;
	id: string;
	intended_version: string;
	actual_version: string | null;
	status: DriftStatus;
}

/** Configured observability endpoints (GET /observability). Empty strings when unconfigured. */
export interface Observability {
	collector_endpoint: string;
	jaeger_url: string;
	grafana_url: string;
}

/** One artifact in an instance's observed state — its content + hash (serve GET /fleet/state). */
export interface InstanceArtifact {
	id: string;
	version: string;
	content_hash: string;
	content: unknown;
}

/** An instance's full observed state — every artifact's content, not just names (design 19). */
export interface InstanceState {
	apiVersion: string;
	kind: string;
	workspace_id: string;
	schema_version: string;
	generated_at?: string;
	artifacts: {
		topologies: InstanceArtifact[];
		skills: InstanceArtifact[];
		archetypes: InstanceArtifact[];
		triggers: InstanceArtifact[];
	};
	providers: string[];
	governance_provider: string;
	health: Record<string, unknown>;
}

/** GET /instances/{id}/state — the panel's cached snapshot + when it was pulled. */
export interface CachedState {
	state: InstanceState;
	synced_at: string;
}

/** POST /instances/{id}/sync — result of a fresh pull. */
export interface SyncResult {
	instance_id: string;
	synced_at: string;
	counts: Record<string, number>;
}

/** POST /instances/{id}/register — the enrollment handshake result (design 19, Phase 2). The
 * credential itself is stored encrypted on the panel and never returned; only its metadata is. */
export interface RegisterResult {
	membership_id: string;
	scope: string;
	fingerprint: string;
	synced_at: string;
	counts: Record<string, number>;
}

/** POST /instances/{id}/refresh — rotated-credential metadata (the new key never leaves the panel). */
export interface RefreshResult {
	membership_id: string;
	scope: string;
	fingerprint: string;
}
