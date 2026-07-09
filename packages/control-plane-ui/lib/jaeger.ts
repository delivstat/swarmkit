/** An instance's OTel `service.name`, mirroring the runtime formula (design:
 * runtime/otel-trace-export): `"<name> (<id>)"` when a workspace name is set, else the id. Keeping
 * this in lockstep with the runtime is what makes the Jaeger deep-link resolve to a real service. */
export function telemetryServiceName(name: string, id: string): string {
	return name ? `${name} (${id})` : id;
}

/** Build a Jaeger UI search URL scoped to one service. Returns null when either the base URL (panel
 * `observability.jaeger_url`) or the service is missing, so callers can hide the link cleanly. */
export function jaegerServiceUrl(
	baseUrl: string,
	service: string,
): string | null {
	if (!baseUrl || !service) return null;
	const base = baseUrl.replace(/\/+$/, "");
	return `${base}/search?service=${encodeURIComponent(service)}&lookback=1h&limit=20`;
}
