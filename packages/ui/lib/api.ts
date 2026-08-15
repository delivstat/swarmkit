import type {
	ArchetypeDetail,
	AuditEvent,
	CanaryStatus,
	Comprehension,
	ConversationDetail,
	ConversationListItem,
	DecisionOutcome,
	GateCoverage,
	GateStatusDetail,
	HealthResponse,
	JobListItem,
	JobResponse,
	JobUsage,
	MemoryChange,
	MemoryItem,
	MemoryQuarantineItem,
	PersistedJob,
	ReviewGate,
	SagaDetail,
	SagaNodeArtifact,
	SagaSummary,
	SendMessageResponse,
	SkillDetail,
	SkillItem,
	StopJobResponse,
	StorageReport,
	SystemReport,
	TopologyDetail,
	TraceSpan,
	TriggerConfig,
	UsageSummary,
	ValidateResponse,
	Whoami,
} from "./types";

import { getAccessToken, handleUnauthorized } from "./token-store";

// Relative by default: when the portal is served by `swarmkit serve` itself, API calls resolve
// against the page's own origin — same serve, same workspace, no env var, no CORS. Set
// NEXT_PUBLIC_SWARMKIT_API only for a detached portal pointing at a remote serve.
const BASE = process.env.NEXT_PUBLIC_SWARMKIT_API ?? "";

/** Merge the current bearer (OIDC token or stored API key) into request headers, if any. */
function authHeaders(extra?: Record<string, string>): Record<string, string> {
	const token = getAccessToken();
	return {
		...(extra ?? {}),
		...(token ? { Authorization: `Bearer ${token}` } : {}),
	};
}

/** A 401 on a request we DID authenticate means the token expired/was rejected → trigger re-auth.
 * Guarded on `token` so a pre-auth request never loops the login. */
function on401(status: number): void {
	if (status === 401 && getAccessToken()) handleUnauthorized();
}

async function get<T>(path: string): Promise<T> {
	const res = await fetch(`${BASE}${path}`, { headers: authHeaders() });
	if (!res.ok) {
		on401(res.status);
		const body = await res.text();
		throw new Error(`${res.status} ${res.statusText}: ${body}`);
	}
	return res.json() as Promise<T>;
}

async function send<T>(
	method: "POST" | "PUT",
	path: string,
	body?: unknown,
): Promise<T> {
	const res = await fetch(`${BASE}${path}`, {
		method,
		headers: authHeaders({ "Content-Type": "application/json" }),
		body: body ? JSON.stringify(body) : undefined,
	});
	if (!res.ok) {
		on401(res.status);
		const text = await res.text();
		throw new Error(`${res.status} ${res.statusText}: ${text}`);
	}
	return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) => send<T>("POST", path, body);
// Artifact writes are PUT /api/<kind>/<id> — a POST to those routes 405s (silent save failure).
const put = <T>(path: string, body?: unknown) => send<T>("PUT", path, body);

export const api = {
	health: () => get<HealthResponse>("/health"),
	storage: () => get<StorageReport>("/storage"),
	system: () => get<SystemReport>("/system"),
	topologies: () => get<string[]>("/topologies"),
	skills: () => get<SkillItem[]>("/skills"),
	archetypes: () => get<string[]>("/archetypes"),
	// Funnel artifacts (design/details/gate-funnel.md). These mirror the topology/skill/archetype
	// CRUD naming. The runtime may not serve them yet — callers treat them best-effort (a failed
	// list leaves the picker empty; a failed save surfaces a "not wired" notice).
	funnels: () => get<string[]>("/funnels"),
	// Contract artifacts (design/details/contract-registry.md) — integration contracts a stage's
	// `locks` reference (a checked, pickable vocabulary instead of free strings). Same best-effort
	// posture as funnels: the CRUD routes may not be wired yet, so callers gate on them.
	contracts: () => get<string[]>("/contracts"),
	validate: () => get<ValidateResponse>("/validate"),
	triggers: () => get<TriggerConfig[]>("/triggers"),
	canary: () => get<CanaryStatus>("/canary"),

	jobs: () => get<JobListItem[]>("/jobs"),
	/** Durable job rows. `/jobs` is the in-memory store — this is what survives a restart. */
	/** Every recorded run, or just one pipeline run's stages when `correlationId` is given. */
	jobsHistory: (correlationId?: string) =>
		get<PersistedJob[]>(
			correlationId
				? `/jobs/history?correlation_id=${encodeURIComponent(correlationId)}`
				: "/jobs/history",
		),
	job: (id: string) => get<JobResponse>(`/jobs/${id}`),
	/**
	 * Ask a running job to stop at its next agent boundary
	 * (design/details/stopping-a-run.md). Not a kill: the run keeps everything it has already done
	 * and is resumable, and a call in flight finishes first — the UI says so rather than implying
	 * the job is dead, because an operator who believes that and starts a replacement gets two runs
	 * writing the same artifacts.
	 */
	stopJob: (id: string) => post<StopJobResponse>(`/jobs/${id}/stop`),
	/** Continue a job parked on a gate (`deferred`) or stopped by a human (`stopped`). */
	resumeJob: (id: string) => post<JobResponse>(`/jobs/${id}/resume`),
	jobUsage: (id: string) => get<JobUsage>(`/usage/${id}`),
	jobStreamUrl: (id: string) => `${BASE}/jobs/${id}/stream`,

	schema: (artifactType: string) =>
		get<Record<string, unknown>>(`/api/schema/${artifactType}`),

	runTrace: (id: string) => get<TraceSpan>(`/observability/runs/${id}/trace`),
	audit: (
		params: { run_id?: string; agent_id?: string; limit?: number } = {},
	) => {
		const q = new URLSearchParams();
		if (params.run_id) q.set("run_id", params.run_id);
		if (params.agent_id) q.set("agent_id", params.agent_id);
		if (params.limit) q.set("limit", String(params.limit));
		const qs = q.toString();
		return get<AuditEvent[]>(`/audit${qs ? `?${qs}` : ""}`);
	},

	run: (topology: string, input: string, maxSteps = 10) =>
		post<JobResponse>(`/run/${topology}`, {
			input,
			max_steps: maxSteps,
		}),

	canaryPromote: (topology: string, version: string) =>
		post<{ promoted: boolean }>(`/canary/${topology}/promote`, {
			version,
		}),
	canaryRollback: (topology: string) =>
		post<{ rolled_back: boolean }>(`/canary/${topology}/rollback`),

	reviewPending: (params: { kind?: string; gate_id?: string } = {}) => {
		const q = new URLSearchParams();
		if (params.kind) q.set("kind", params.kind);
		if (params.gate_id) q.set("gate_id", params.gate_id);
		const suffix = q.toString() ? `?${q}` : "";
		return get<ReviewGate[]>(`/review${suffix}`);
	},
	reviewApprove: (id: string, comment = "") =>
		post<ReviewGate>(`/review/${id}/approve`, { comment }),
	reviewReject: (id: string, comment = "") =>
		post<ReviewGate>(`/review/${id}/reject`, { comment }),
	reviewAnswer: (id: string, answer: string, comment = "") =>
		post<ReviewGate>(`/review/${id}/answer`, { answer, comment }),
	/** Resolve a multi-party role-task. Deliberately takes no identity: the resolver is the
	 * authenticated session, and the server rejects a body-supplied one. */
	reviewResolve: (id: string, outcome: DecisionOutcome, comment = "") =>
		post<ReviewGate>(`/review/${id}/resolve`, { outcome, comment }),
	whoami: () => get<Whoami>("/whoami"),

	conversations: () => get<ConversationListItem[]>("/conversations"),
	conversation: (id: string) => get<ConversationDetail>(`/conversations/${id}`),
	createConversation: (topology: string) =>
		post<{ id: string; topology: string }>("/conversations", { topology }),
	sendMessageStream: (
		conversationId: string,
		message: string,
		onProgress: (text: string) => void,
	): Promise<SendMessageResponse & { events?: unknown[] }> =>
		new Promise((resolve, reject) => {
			fetch(`${BASE}/conversations/${conversationId}/messages`, {
				method: "POST",
				headers: authHeaders({ "Content-Type": "application/json" }),
				body: JSON.stringify({ message }),
			})
				.then((res) => {
					if (!res.ok) {
						res.text().then((t) => reject(new Error(`${res.status}: ${t}`)));
						return;
					}
					const reader = res.body?.getReader();
					if (!reader) {
						reject(new Error("No response body"));
						return;
					}
					const decoder = new TextDecoder();
					let buffer = "";

					function pump(): void {
						reader?.read().then(({ done, value }) => {
							if (done) return;
							buffer += decoder.decode(value, { stream: true });
							const lines = buffer.split("\n");
							buffer = lines.pop() ?? "";
							for (const line of lines) {
								if (!line.startsWith("data: ")) continue;
								try {
									const data = JSON.parse(line.slice(6));
									if (data.type === "progress") {
										onProgress(data.text);
									} else if (data.type === "done") {
										resolve(data);
									} else if (data.type === "error") {
										reject(new Error(data.error));
									}
								} catch {
									// skip malformed lines
								}
							}
							pump();
						});
					}
					pump();
				})
				.catch(reject);
		}),

	usage: () => get<UsageSummary>("/usage"),

	topologyDetail: (id: string) => get<TopologyDetail>(`/api/topologies/${id}`),
	topologyYaml: (id: string) =>
		get<{ yaml: string }>(`/api/topologies/${id}/yaml`),
	archetypeDetail: (id: string) =>
		get<ArchetypeDetail>(`/api/archetypes/${id}`),
	skillDetail: (id: string) => get<SkillDetail>(`/api/skills/${id}`),
	saveTopology: (id: string, yaml: string, dryRun = false) =>
		put<{ valid: boolean; errors?: { code: string; message: string }[] }>(
			`/api/topologies/${id}`,
			{ yaml, dry_run: dryRun },
		),
	saveSkill: (id: string, yaml: string) =>
		put<{ valid: boolean; errors?: { code: string; message: string }[] }>(
			`/api/skills/${id}`,
			{ yaml },
		),
	saveArchetype: (id: string, yaml: string) =>
		put<{ valid: boolean; errors?: { code: string; message: string }[] }>(
			`/api/archetypes/${id}`,
			{ yaml },
		),
	funnelYaml: (id: string) => get<{ yaml: string }>(`/api/funnels/${id}/yaml`),
	saveFunnel: (id: string, yaml: string) =>
		put<{ valid: boolean; errors?: { code: string; message: string }[] }>(
			`/api/funnels/${id}`,
			{ yaml },
		),
	getComprehension: (fastApproveSeconds?: number) =>
		get<Comprehension>(
			fastApproveSeconds != null
				? `/comprehension?fast_approve_seconds=${fastApproveSeconds}`
				: "/comprehension",
		),
	contractYaml: (id: string) =>
		get<{ yaml: string }>(`/api/contracts/${id}/yaml`),
	saveContract: (id: string, yaml: string) =>
		put<{ valid: boolean; errors?: { code: string; message: string }[] }>(
			`/api/contracts/${id}`,
			{ yaml },
		),
	/** Rebuild the runtime from disk. Returns the workspace's validation AFTER the attempt — and
	 * note a failed reload leaves the PREVIOUS runtime serving, so `valid: false` means the change
	 * on disk is not live rather than that a broken config is. */
	reloadWorkspace: () => post<ValidateResponse>("/api/reload"),

	// Governed memory (design/details/governed-memory.md) — the same store `swarmkit memory` uses.
	searchMemory: (query = "", type?: string, limit = 100) => {
		const p = new URLSearchParams({ query, limit: String(limit) });
		if (type) p.set("type", type);
		return get<{ memories: MemoryItem[] }>(`/memory?${p.toString()}`);
	},
	getMemoryItem: (subject: string, attribute: string, history = true) => {
		const p = new URLSearchParams({
			subject,
			attribute,
			history: String(history),
		});
		return get<{ current: MemoryItem | null; history: MemoryChange[] }>(
			`/memory/item?${p.toString()}`,
		);
	},
	listQuarantine: (status = "pending") =>
		get<{ quarantine: MemoryQuarantineItem[] }>(
			`/memory/quarantine?status=${status}`,
		),
	resolveQuarantine: (id: number, resolvedBy: string, accept: boolean) =>
		post<{
			resolved: boolean;
			accepted: boolean;
			outcome: { op: string; value: string } | null;
		}>(`/memory/quarantine/${id}/resolve`, { resolved_by: resolvedBy, accept }),
};
