export interface HealthResponse {
	status: string;
	workspace: string;
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

/** A pending harness gate (GET /review) — a §6.2 permission or §6.3 input request awaiting a human. */
export interface ReviewGate {
	id: string;
	kind: "permission" | "input" | "other";
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
	timestamp: string;
}
