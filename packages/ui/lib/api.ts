import type {
	ArchetypeDetail,
	CanaryStatus,
	ConversationDetail,
	ConversationListItem,
	HealthResponse,
	JobListItem,
	JobResponse,
	SendMessageResponse,
	SkillDetail,
	SkillItem,
	TopologyDetail,
	TriggerConfig,
	UsageSummary,
	ValidateResponse,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_SWARMKIT_API ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
	const res = await fetch(`${BASE}${path}`);
	if (!res.ok) {
		const body = await res.text();
		throw new Error(`${res.status} ${res.statusText}: ${body}`);
	}
	return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
	const res = await fetch(`${BASE}${path}`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: body ? JSON.stringify(body) : undefined,
	});
	if (!res.ok) {
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
	jobStreamUrl: (id: string) => `${BASE}/jobs/${id}/stream`,

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
				headers: { "Content-Type": "application/json" },
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
	reloadWorkspace: () => post<{ valid: boolean }>("/api/reload"),
};
