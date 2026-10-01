// Thin client for the SwarmKit runtime. Every call carries the runtime bearer (identifying the
// caller as the owner's client_id — design/details/dot-app.md §Auth model surface 2) plus, when
// widening beyond the caller, an X-Owner header the runtime uses to resolve per-user creds.
//
// Commit 4 fills in invokeMcpTool for the fast lane; later commits grow the surface (getRun,
// startRun, listRuns, getUsageAggregate, workspace config, OAuth).

export interface SwarmKitClientOptions {
	baseUrl: string;
	token: string;
	owner: string;
}

export class SwarmKitClient {
	constructor(private readonly opts: SwarmKitClientOptions) {}

	// Fast lane. Wraps POST /api/mcp/{server_id}/invoke (runtime v1.260.0+). The runtime
	// resolves the owner's OAuth token from its store, calls the tool on their behalf and
	// audit-logs it. Nothing about DOT sees a Google token.
	async invokeMcpTool<T = unknown>(
		serverId: string,
		tool: string,
		args: Record<string, unknown>,
	): Promise<T> {
		const url = new URL(
			`/api/mcp/${encodeURIComponent(serverId)}/invoke`,
			this.opts.baseUrl,
		);
		const res = await fetch(url, {
			method: "POST",
			headers: this.headers(),
			body: JSON.stringify({ tool, arguments: args }),
		});
		if (!res.ok) {
			const detail = await res.text().catch(() => "");
			throw new SwarmKitError(
				`invokeMcpTool ${serverId}.${tool} failed: ${res.status} ${detail}`,
				res.status,
			);
		}
		const body = (await res.json()) as { result: T };
		return body.result;
	}

	// GET /api/oauth/my-credentials (from #982). Returns rows per provider with expiry.
	async getMyCredentials(): Promise<MyCredential[]> {
		const url = new URL("/api/oauth/my-credentials", this.opts.baseUrl);
		const res = await fetch(url, { headers: this.headers() });
		if (!res.ok) {
			throw new SwarmKitError(
				`getMyCredentials failed: ${res.status}`,
				res.status,
			);
		}
		const body = (await res.json()) as { credentials: MyCredential[] };
		return body.credentials;
	}

	// POST /oauth/{provider}/start with a return_to; returns Google's auth URL. The runtime does
	// the token exchange on Google's callback and 302s back to return_to.
	async startOAuth(provider: string, returnTo: string): Promise<string> {
		const url = new URL(
			`/oauth/${encodeURIComponent(provider)}/start`,
			this.opts.baseUrl,
		);
		const res = await fetch(url, {
			method: "POST",
			headers: this.headers(),
			body: JSON.stringify({ return_to: returnTo }),
		});
		if (!res.ok) {
			throw new SwarmKitError(`startOAuth failed: ${res.status}`, res.status);
		}
		const body = (await res.json()) as { auth_url: string };
		return body.auth_url;
	}

	// Wraps GET /audit — confirmed live surface (design §Runtime dependencies).
	async listRuns(params: {
		topologies?: string[];
		cursor?: string;
		limit?: number;
	}): Promise<AuditListResult> {
		const url = new URL("/audit", this.opts.baseUrl);
		if (params.topologies) {
			for (const t of params.topologies) url.searchParams.append("topology", t);
		}
		if (params.cursor) url.searchParams.set("cursor", params.cursor);
		if (params.limit) url.searchParams.set("limit", String(params.limit));
		const res = await fetch(url, { headers: this.headers() });
		if (!res.ok) {
			throw new SwarmKitError(`listRuns failed: ${res.status}`, res.status);
		}
		return (await res.json()) as AuditListResult;
	}

	async getRunDetail(runId: string): Promise<AuditDetail> {
		const url = new URL(
			`/audit/${encodeURIComponent(runId)}`,
			this.opts.baseUrl,
		);
		const res = await fetch(url, { headers: this.headers() });
		if (!res.ok) {
			throw new SwarmKitError(`getRunDetail failed: ${res.status}`, res.status);
		}
		return (await res.json()) as AuditDetail;
	}

	async getUsageSummary(): Promise<unknown> {
		return this.json("/api/usage/summary");
	}

	async getUsageDaily(window: number): Promise<unknown> {
		return this.json(`/api/usage/daily?window=${window}`);
	}

	async getUsageBreakdown(by: string, window: string): Promise<unknown> {
		return this.json(
			`/api/usage/breakdown?by=${encodeURIComponent(by)}&window=${encodeURIComponent(window)}`,
		);
	}

	private async json<T = unknown>(path: string): Promise<T> {
		const url = new URL(path, this.opts.baseUrl);
		const res = await fetch(url, { headers: this.headers() });
		if (!res.ok) {
			throw new SwarmKitError(`${path} failed: ${res.status}`, res.status);
		}
		return (await res.json()) as T;
	}

	async getWorkspaceConfig<T = unknown>(): Promise<T> {
		const url = new URL("/api/workspace-config", this.opts.baseUrl);
		const res = await fetch(url, { headers: this.headers() });
		if (!res.ok) {
			throw new SwarmKitError(
				`getWorkspaceConfig failed: ${res.status}`,
				res.status,
			);
		}
		return (await res.json()) as T;
	}

	async patchWorkspaceConfig<T = unknown>(patch: unknown): Promise<T> {
		const url = new URL("/api/workspace-config", this.opts.baseUrl);
		const res = await fetch(url, {
			method: "PATCH",
			headers: this.headers(),
			body: JSON.stringify(patch),
		});
		if (!res.ok) {
			const txt = await res.text().catch(() => "");
			throw new SwarmKitError(
				`patchWorkspaceConfig failed: ${res.status} ${txt}`,
				res.status,
			);
		}
		return (await res.json()) as T;
	}

	async disconnectOAuth(provider: string): Promise<void> {
		const url = new URL(
			`/api/oauth/credentials/${encodeURIComponent(provider)}`,
			this.opts.baseUrl,
		);
		const res = await fetch(url, {
			method: "DELETE",
			headers: this.headers(),
		});
		if (!res.ok && res.status !== 404) {
			throw new SwarmKitError(
				`disconnectOAuth failed: ${res.status}`,
				res.status,
			);
		}
	}

	private headers(): Record<string, string> {
		return {
			Authorization: `Bearer ${this.opts.token}`,
			"X-Owner": this.opts.owner,
			"Content-Type": "application/json",
		};
	}
}

export interface MyCredential {
	provider: string;
	owner: string;
	expires_at: string | null;
	expired: boolean;
}

export interface AuditListResult {
	entries: AuditEntry[];
	next_cursor: string | null;
}

export interface AuditEntry {
	run_id: string;
	topology_id: string;
	started_at: string;
	elapsed_ms: number;
	status: "success" | "error" | "running";
	summary: string;
}

export interface AuditDetail extends AuditEntry {
	archetypes: {
		id: string;
		elapsed_ms: number;
		status: "success" | "error" | "running";
	}[];
	errors: { archetype: string; message: string }[];
}

export class SwarmKitError extends Error {
	constructor(
		message: string,
		public readonly status: number,
	) {
		super(message);
		this.name = "SwarmKitError";
	}
}

export function clientFromEnv(owner: string): SwarmKitClient | null {
	const baseUrl = process.env.SWARMKIT_RUNTIME_URL;
	const token = process.env.SWARMKIT_RUNTIME_TOKEN;
	if (!baseUrl || !token) return null;
	return new SwarmKitClient({ baseUrl, token, owner });
}
