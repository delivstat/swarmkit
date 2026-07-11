import type {
	ArchetypeDetail,
	AuditEvent,
	CanaryStatus,
	ConversationDetail,
	ConversationListItem,
	HealthResponse,
	JobListItem,
	JobResponse,
	JobUsage,
	SendMessageResponse,
	SkillDetail,
	SkillItem,
	TopologyDetail,
	TraceSpan,
	TriggerConfig,
	UsageSummary,
	ValidateResponse,
} from "./types";

import { getAccessToken, handleUnauthorized } from "./token-store";

const BASE = process.env.NEXT_PUBLIC_SWARMKIT_API ?? "http://localhost:8000";

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

async function post<T>(path: string, body?: unknown): Promise<T> {
	const res = await fetch(`${BASE}${path}`, {
		method: "POST",
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

export const api = {
	health: () => get<HealthResponse>("/health"),
	topologies: () => get<string[]>("/topologies"),
	skills: () => get<SkillItem[]>("/skills"),
	archetypes: () => get<string[]>("/archetypes"),
	validate: () => get<ValidateResponse>("/validate"),
	triggers: () => get<TriggerConfig[]>("/triggers"),
	canary: () => get<CanaryStatus>("/canary"),

	jobs: () => get<JobListItem[]>("/jobs"),
	job: (id: string) => get<JobResponse>(`/jobs/${id}`),
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
		post<{ valid: boolean; errors?: { code: string; message: string }[] }>(
			`/api/topologies/${id}`,
			{ yaml, dry_run: dryRun },
		),
	saveSkill: (id: string, yaml: string) =>
		post<{ valid: boolean; errors?: { code: string; message: string }[] }>(
			`/api/skills/${id}`,
			{ yaml },
		),
	saveArchetype: (id: string, yaml: string) =>
		post<{ valid: boolean; errors?: { code: string; message: string }[] }>(
			`/api/archetypes/${id}`,
			{ yaml },
		),
	reloadWorkspace: () => post<{ valid: boolean }>("/api/reload"),
};
