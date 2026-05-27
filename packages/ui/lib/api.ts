import type {
	CanaryStatus,
	HealthResponse,
	JobListItem,
	JobResponse,
	SkillItem,
	TriggerConfig,
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
};
