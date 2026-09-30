// Thin client for the SwarmKit runtime. Every call carries the runtime bearer (identifying the
// caller as the owner's client_id — design/details/dot-app.md §Auth model surface 2) and, when
// widening beyond the caller, an X-Owner header the runtime uses to resolve per-user creds.
//
// Only endpoints commit 2 needs are stubbed. Commits 4–9 grow the surface (invokeMcpTool, brief,
// items, connections, settings, activity, usage).

export interface SwarmKitClientOptions {
	baseUrl: string;
	token: string;
	owner: string;
}

export class SwarmKitClient {
	constructor(private readonly opts: SwarmKitClientOptions) {}

	async whoami(): Promise<{ ok: boolean }> {
		const res = await fetch(new URL("/api/auth/whoami", this.opts.baseUrl), {
			headers: this.headers(),
		});
		return { ok: res.ok };
	}

	private headers(): Record<string, string> {
		return {
			Authorization: `Bearer ${this.opts.token}`,
			"X-Owner": this.opts.owner,
			"Content-Type": "application/json",
		};
	}
}

export function clientFromEnv(owner: string): SwarmKitClient | null {
	const baseUrl = process.env.SWARMKIT_RUNTIME_URL;
	const token = process.env.SWARMKIT_RUNTIME_TOKEN;
	if (!baseUrl || !token) return null;
	return new SwarmKitClient({ baseUrl, token, owner });
}
