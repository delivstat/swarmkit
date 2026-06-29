import { getAccessToken, handleUnauthorized } from "./token-store";
import type { Command, Instance, MintResult } from "./types";

/**
 * Base URL of the swarmkit-control-plane API. Configured via NEXT_PUBLIC_CONTROL_PLANE_API;
 * defaults to same-origin (relative requests) when unset — no host is hardcoded.
 */
const API_BASE = process.env.NEXT_PUBLIC_CONTROL_PLANE_API ?? "";

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
	const token = getAccessToken();
	const res = await fetch(`${API_BASE}${path}`, {
		...init,
		headers: {
			"Content-Type": "application/json",
			...(token ? { Authorization: `Bearer ${token}` } : {}),
			...init?.headers,
		},
		cache: "no-store",
	});
	if (res.status === 401 && token) {
		// We sent a token and it was rejected (expired/invalid) — re-initiate login. Guarded on
		// `token` so a pre-auth request never triggers a redirect loop.
		handleUnauthorized();
	}
	if (!res.ok) {
		throw new ApiError(
			`${init?.method ?? "GET"} ${path} → ${res.status}`,
			res.status,
		);
	}
	return (await res.json()) as T;
}

export interface EnrollBody {
	name: string;
	endpoint: string;
	connection: "direct" | "poll";
	tier: string;
	token_ref?: string;
}

export const api = {
	health: () => request<{ status: string }>("/health"),
	listInstances: () => request<Instance[]>("/instances"),
	enrollInstance: (body: EnrollBody) =>
		request<Instance>("/instances", {
			method: "POST",
			body: JSON.stringify(body),
		}),
	getInstance: (id: string) => request<Instance>(`/instances/${id}`),
	deleteInstance: (id: string) =>
		request<{ deleted: string }>(`/instances/${id}`, { method: "DELETE" }),
	mintToken: (id: string, body: { tier?: string; client_name?: string } = {}) =>
		request<MintResult>(`/instances/${id}/mint-token`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
	verifyInstance: (id: string) =>
		request<Instance>(`/instances/${id}/verify`, { method: "POST" }),
	listCommands: (id: string) => request<Command[]>(`/instances/${id}/commands`),
	enqueueCommand: (
		id: string,
		body: { verb: string; args?: Record<string, unknown> },
	) =>
		request<Command>(`/instances/${id}/commands`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
};
