import type { Instance } from "./types";

/** Base URL of the swarmkit-control-plane API (default: local dev on :8800). */
const API_BASE =
	process.env.NEXT_PUBLIC_CONTROL_PLANE_API ?? "http://localhost:8800";

export class ApiError extends Error {
	constructor(
		message: string,
		readonly status: number,
	) {
		super(message);
		this.name = "ApiError";
	}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const res = await fetch(`${API_BASE}${path}`, {
		...init,
		headers: { "Content-Type": "application/json", ...init?.headers },
		cache: "no-store",
	});
	if (!res.ok) {
		throw new ApiError(
			`${init?.method ?? "GET"} ${path} → ${res.status}`,
			res.status,
		);
	}
	return (await res.json()) as T;
}

export const api = {
	health: () => request<{ status: string }>("/health"),
	listInstances: () => request<Instance[]>("/instances"),
	getInstance: (id: string) => request<Instance>(`/instances/${id}`),
	deleteInstance: (id: string) =>
		request<{ deleted: string }>(`/instances/${id}`, { method: "DELETE" }),
};
