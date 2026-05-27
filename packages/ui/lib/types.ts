export interface HealthResponse {
	status: string;
	workspace: string;
}

export interface JobResponse {
	job_id: string;
	status: "pending" | "running" | "completed" | "failed";
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
