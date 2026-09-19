/** A2A federation deep-link (a2a-federation.md): from an `a2a.remote_usage` audit event, build a
 * link to the callee instance's own job-detail page, so the waterfall/audit can open the remote
 * run rather than dead-ending at "called agent, got text". */

export interface RemoteRunLink {
	href: string;
	label: string;
	costUsd: number | null;
}

/** Build the deep-link for an `a2a.remote_usage` payload, or null when it carries no remote run.
 *
 * The payload records the callee's A2A `endpoint` (e.g. `https://remote/a2a`) and its `remote_run_id`.
 * The callee's portal is same-origin as its serve, so stripping the trailing `/a2a` yields the base
 * and `{base}/job?id={run}` opens the remote job detail. A non-SwarmKit remote records neither, so
 * this returns null and the row renders as a plain event. */
export function remoteRunLink(
	payload: Record<string, unknown>,
): RemoteRunLink | null {
	const runId =
		typeof payload.remote_run_id === "string" ? payload.remote_run_id : "";
	const endpoint = typeof payload.endpoint === "string" ? payload.endpoint : "";
	if (!runId || !endpoint) return null;
	let base: string;
	try {
		const u = new URL(endpoint);
		// Strip the A2A mount — `/a2a` or a per-topology `/a2a/<name>` — to get the serve/portal base.
		base = u.origin + u.pathname.replace(/\/a2a(\/[^/]+)?\/?$/, "");
	} catch {
		return null; // a malformed endpoint is not a link we can trust
	}
	const card =
		typeof payload.card === "string" && payload.card
			? payload.card
			: "remote agent";
	const cost = typeof payload.cost_usd === "number" ? payload.cost_usd : null;
	return {
		href: `${base.replace(/\/$/, "")}/job?id=${encodeURIComponent(runId)}`,
		label: `open run on ${card}`,
		costUsd: cost,
	};
}
