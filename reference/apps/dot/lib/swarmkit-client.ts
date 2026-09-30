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

	private headers(): Record<string, string> {
		return {
			Authorization: `Bearer ${this.opts.token}`,
			"X-Owner": this.opts.owner,
			"Content-Type": "application/json",
		};
	}
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
