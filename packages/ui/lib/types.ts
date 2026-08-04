export interface HealthResponse {
	status: string;
	workspace: string;
	/** The runtime hosting this portal. Empty when serve runs uninstalled from a source tree. */
	runtime_version?: string;
	/** The portal's own version — a SEPARATE package from the runtime, so one number cannot
	 * answer "what version am I running". Empty when serve runs headless. */
	webui_version?: string;
}

/** One row of GET /storage — where a single store's data actually lives. */
export interface StorageTarget {
	store: string;
	backend: string;
	/** Redacted for postgres (the URL carries a password); "workspace-local" for sqlite. */
	location: string;
	/** Which setting won: env, storage.<kind>, storage.runtime, or default. */
	source: string;
}

export interface StorageReport {
	stores: StorageTarget[];
	/** Configured remote, but a populated local SQLite still exists — rows the UI cannot see. */
	warnings: string[];
}

/** One known environment variable, from GET /system. Curated — never a raw os.environ dump, so a
 * credential cannot reach this page by accident. Secret values report only set/unset. */
export interface EnvVarStatus {
	name: string;
	group: string;
	description: string;
	set: boolean;
	/** Masked for URLs, the literal "set" for secrets, null when unset. */
	value: string | null;
	sensitive: boolean;
}

/** One resolved entry from the workspace's own workspace.env.yaml. Read from the file on every
 * request, so a parameter a new feature introduces appears here with no code change. */
export interface WorkspaceProperty {
	name: string;
	/** Resolved (${ENV_VAR} already substituted). "set" when the NAME suggests a credential. */
	value: string;
	sensitive: boolean;
}

export interface SystemReport {
	workspace: string;
	runtime_version?: string;
	webui_version?: string;
	storage: StorageReport;
	environment: EnvVarStatus[];
	properties: WorkspaceProperty[];
}

export interface JobResponse {
	job_id: string;
	status: "pending" | "running" | "completed" | "failed";
	topology: string;
	output: string | null;
	error: string | null;
}

export interface JobListItem {
	job_id: string;
	topology: string;
	version: string | null;
	status: "pending" | "running" | "completed" | "failed";
	created_at: string;
	completed_at: string | null;
}

/** A job as recorded in the durable store — survives a restart, and carries usage.
 *
 * `/jobs` is the in-memory JobStore: only this process, gone on restart. `/jobs/history` is this.
 * Both contain a job from the moment it is created, so the two lists overlap while it runs. */
export interface PersistedJob {
	job_id: string;
	topology: string;
	version: string | null;
	status: "pending" | "running" | "completed" | "failed";
	created_at: string;
	completed_at: string | null;
	usage_input_tokens: number | null;
	usage_output_tokens: number | null;
	usage_cost_usd: number | null;
}

export interface SkillItem {
	id: string;
	category: string;
}

export interface ValidateResponse {
	valid: boolean;
	workspace_id: string;
	topologies: string[];
	skills: string[];
	archetypes: string[];
}

export interface CanaryVersionMetrics {
	total_runs: number;
	failed_runs: number;
	error_rate: number;
	avg_drift: number;
	window_start: string;
}

export interface CanaryPromoteCriteria {
	min_runs: number;
	error_rate_below: number;
	drift_below: number;
	window_minutes: number;
}

export interface CanaryVersionStatus {
	version: string;
	weight: number;
	metrics?: CanaryVersionMetrics;
	promote_when?: CanaryPromoteCriteria;
}

export interface CanaryRouteStatus {
	topology: string;
	versions: CanaryVersionStatus[];
}

export interface CanaryPromotion {
	topology: string;
	promoted_version: string;
	old_weights: string;
	metrics: string;
	timestamp: string;
}

export interface CanaryStatus {
	enabled: boolean;
	routes: CanaryRouteStatus[];
	promotions: CanaryPromotion[];
}

export interface TriggerConfig {
	id: string;
	type: string;
	enabled: boolean;
	targets: string[];
	config: Record<string, unknown>;
}

export interface ConversationListItem {
	id: string;
	topology: string;
	turns: string;
	updated: string;
	last_message: string;
}

export interface TurnUsage {
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	by_model: Record<string, { input: number; output: number }>;
}

export interface TraceToolCall {
	tool_name: string;
	arguments: Record<string, unknown>;
	result_length: number;
	duration_ms: number;
	error: string | null;
}

export interface TraceAgentStep {
	agent_id: string;
	model: string;
	duration_ms: number;
	input_tokens: number;
	output_tokens: number;
	tool_calls: TraceToolCall[];
}

export interface TraceData {
	run_id: string;
	duration_ms: number;
	llm_calls: number;
	agent_steps: TraceAgentStep[];
}

export interface ConversationTurn {
	role: "human" | "swarm";
	content: string;
	timestamp: string;
	usage?: TurnUsage;
	events?: { event_type: string; agent_id: string }[];
	trace?: TraceData;
}

export interface ConversationDetail {
	id: string;
	topology: string;
	turns: ConversationTurn[];
	created_at: string;
	updated_at: string;
}

export interface SendMessageResponse {
	output: string;
	turns: number;
	conversation_id: string;
}

/** A node in a run's span tree (GET /observability/runs/{id}/trace) — for the waterfall. */
export interface TraceSpan {
	name: string;
	start_ns: number;
	end_ns: number;
	duration_ms: number;
	attributes: Record<string, unknown>;
	error: string | null;
	children: TraceSpan[];
}

/** An append-only audit event (GET /audit) — read-only. */
export interface AuditEvent {
	event_id: string;
	event_type: string;
	agent_id: string;
	agent_role: string | null;
	timestamp: string | null;
	topology_id: string | null;
	skill_id: string | null;
	run_id: string | null;
	payload: Record<string, unknown>;
}

/** Per-run usage totals — the flat shape GET /usage/{job_id} returns (no by-model breakdown). */
export interface JobUsage {
	total_calls: number;
	total_input_tokens: number;
	total_output_tokens: number;
	total_cache_tokens: number;
	total_cost_usd: number;
}

export interface UsageSummary {
	summary: {
		total_calls: number;
		total_input_tokens: number;
		total_output_tokens: number;
		total_cache_tokens: number;
		total_cost_usd: number;
	};
	by_model: {
		model: string;
		calls: number;
		input_tokens: number;
		output_tokens: number;
		cost_usd: number;
	}[];
}

export interface ResolvedAgent {
	id: string;
	role: string;
	source_archetype: string | null;
	model: Record<string, unknown> | null;
	skills: string[];
	/** Optional per-artifact quality gate (design/details/gate-funnel.md): a Funnel id (the topology
	 * schema annotates `funnel` as an `x-swarmkit-ref: funnel`) — or, for a staged canvas edit, the
	 * raw funnel value. Drives the "gated" badge on the agent card. Absent ⇒ no gate. */
	funnel?: string | Record<string, unknown> | null;
	children?: ResolvedAgent[];
}

export interface TopologyDetail {
	id: string;
	version: string;
	description: string | null;
	resolved: ResolvedAgent;
}

export interface ArchetypeDetail {
	id: string;
	name: string;
	description: string;
	role: string;
	defaults: {
		model: Record<string, unknown> | null;
		skills: string[];
	};
}

export interface SkillDetail {
	id: string;
	name: string;
	description: string;
	category: string;
	implementation_type: string | null;
}

/** One stage's outgoing edge in a pipeline's gate coverage (GET /api/pipelines/{id}/gate-coverage).
 * Mirrors the Python payload (snake_case). See design/details/gate-coverage-and-comprehension-debt.md. */
export interface GateCoverageStage {
	stage: string;
	/** "passthrough" (no gate) | "human" (a funnel — always ends in a human approve). */
	gate: "passthrough" | "human";
	funnel: string | null;
	/** subset of ["validate","judge","review"] present on the funnel, weak→strong. */
	pre_filters: string[];
	/** entered by an event no stage emits (CI / a rig / SAST). */
	external_entry: boolean;
	/** nothing downstream consumes this stage's success — no onward edge to gate. */
	terminal: boolean;
	/** the stage's plan-first objective, or null if it declares none (a coverage gap). */
	objective: string | null;
}

/** A pipeline's gate coverage — every stage classified, the narrowest verified edge named. */
export interface GateCoverage {
	pipeline: string;
	verdict: string;
	narrowest: string | null;
	stages: GateCoverageStage[];
}

/** One suspiciously-fast approval in the comprehension telemetry (GET /comprehension). */
export interface ComprehensionFastApprove {
	gate_id: string;
	run_id: string | null;
	latency_seconds: number;
	distinct_approvers: number;
	timestamp: string;
}

/** Comprehension-debt signals from the audit log (read-only, never a gate). Mirrors the Python
 * payload. `threshold_seconds` is the active fast-approve threshold — the UI shows it pre-set. */
export interface Comprehension {
	verdict: string;
	threshold_seconds: number;
	approvals_seen: number;
	fast_approvals: ComprehensionFastApprove[];
	deferred: string[];
}

/** A governed-memory current-state row (GET /memory, /memory/item). Mirrors memory_to_dict. */
export interface MemoryItem {
	key: string;
	subject: string;
	attribute: string;
	value: string;
	type: string;
	confidence: number;
	valid_from: string;
	last_reinforced_at: string;
	reinforce_count: number;
	source: string | null;
	status: string;
}

/** One append-only change on a memory's timeline (GET /memory/item?history=true). */
export interface MemoryChange {
	id: number;
	op: string; // new | reinforce | update | refine | contradict
	before: Record<string, unknown> | null;
	after: Record<string, unknown>;
	reason: string;
	decided_by: string; // deterministic | skill | curator
	timestamp: string;
}

/** A parked contradiction awaiting a curator decision (GET /memory/quarantine). */
export interface MemoryQuarantineItem {
	id: number;
	memory_key: string;
	candidate: Record<string, unknown>;
	current_value: string;
	reasoning: string;
	status: string; // pending | accepted | rejected
	created_at: string;
	resolved_at: string | null;
	resolved_by: string | null;
}

/** A pipeline run's lifecycle status (GET /pipelines/sagas). Domain-neutral saga state. */
export type SagaStatus =
	| "active"
	| "parked"
	| "completed"
	| "rejected"
	| "failed";

/** One pipeline run, list-shape (GET /pipelines/sagas). Searchable by correlation id. */
export interface SagaSummary {
	correlation_id: string;
	graph: string;
	status: SagaStatus;
	current_stage: string | null;
	passed_stages: string[];
	pending_gate_stage: string | null;
	tag: string;
	created_at: string;
	updated_at: string;
}

/** One append-only timeline entry of a run (started / completed / parked / resumed / rejected …). */
export interface SagaTimelineEntry {
	seq: number;
	at: string;
	stage: string | null;
	kind: string;
	detail: string;
}

/** A single pipeline run in full (GET /pipelines/sagas/{id}) — summary + artifacts + timeline. */
export interface SagaDetail extends SagaSummary {
	/** stage id → artifact reference (content is fetched lazily per node). */
	artifacts: Record<string, string>;
	/** stage id → number of attempts (a retry bumps the count). */
	attempts: Record<string, number>;
	timeline: SagaTimelineEntry[];
}

/** A node's input + produced artifact, lazy-loaded on selection
 * (GET /pipelines/sagas/{id}/node/{stage}). */
export interface SagaNodeArtifact {
	stage: string;
	ref: string | null;
	content: string | null;
	/** Reference + content of the input this node received (null if none was recorded). */
	input_ref: string | null;
	input: string | null;
}

/** A pending harness gate (GET /review) — a §6.2 permission or §6.3 input request awaiting a human. */
export interface ReviewGate {
	id: string;
	kind: "permission" | "input" | "role_task" | "other";
	agent_id: string;
	topology_id: string;
	skill_id: string;
	reason: string;
	status: "pending" | "approved" | "rejected";
	answer: string;
	capability: string;
	question: string;
	options: string[];
	free_text_allowed: boolean;
	/** Multi-party role-task fields — empty for the harness kinds. `gate_id` is
	 * `<correlation_id>:<agent_id>`, so a run's gate can be located from an inbox row. */
	gate_id: string;
	role: string;
	scope: string;
	rule_index: number | null;
	resolved_by: string;
	/** What the reviewer said when deciding. Relayed to the agent; may be empty. */
	comment: string;
	/** The artifact this decision is about, and which round of the gate it belongs to. A gate
	 * re-opens on a rework loop against a NEW artifact; a decision from an earlier round is kept
	 * and shown but no longer counts toward quorum. */
	artifact_ref: string;
	round: number;
	timestamp: string;
}

/** What a human can decide at a gate. `reject` ENDS the run; `changes-requested` asks for another
 * attempt, re-running the stage with the comment in its input. */
export type DecisionOutcome = "approve" | "changes-requested" | "reject";

/** One role-task of a gate, as reported by GET /pipelines/gate-status. */
export interface RoleTaskItem {
	id: string;
	role: string;
	scope: string;
	rule_index: number | null;
	status: "pending" | "approved" | "rejected" | "changes-requested";
	resolved_by: string;
	comment?: string;
	artifact_ref?: string;
	round?: number;
}

/** A gate's aggregate resolution plus its per-role detail.
 *
 * `quorum_evaluated` says HOW `status` was derived: `true` means the approval engine evaluated the
 * gate's policy (so it matches the decision the runtime gates on); `false` means the policy was not
 * reachable and the server folded the items, which treats every task approving as the bar. The two
 * differ for any quorum other than `all`. */
export interface GateStatusDetail {
	correlation_id: string;
	gate: string;
	status: "approved" | "rejected" | "pending";
	items: RoleTaskItem[];
	quorum_evaluated: boolean;
}

/** The authenticated caller (GET /whoami). Under `mode: "none"` every caller is `anonymous`. */
export interface Whoami {
	client_id: string;
	client_name: string;
	provider: string;
	scopes: string[];
	mode: string;
}
