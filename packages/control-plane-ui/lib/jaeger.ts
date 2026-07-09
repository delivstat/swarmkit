/** Build a Jaeger UI search URL scoped to one service — an instance's OTel service is its
 * workspace id (runtime sets `service.name = workspace metadata.id`). Returns null when either the
 * base URL (panel `observability.jaeger_url`) or the service is missing, so callers can hide the
 * link cleanly. */
export function jaegerServiceUrl(
	baseUrl: string,
	service: string,
): string | null {
	if (!baseUrl || !service) return null;
	const base = baseUrl.replace(/\/+$/, "");
	return `${base}/search?service=${encodeURIComponent(service)}&lookback=1h&limit=20`;
}
