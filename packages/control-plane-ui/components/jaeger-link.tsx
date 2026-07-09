import { ExternalLink, Waypoints } from "lucide-react";

import { Button } from "@/components/ui/button";
import { jaegerServiceUrl } from "@/lib/jaeger";

/** "View in Jaeger" button — deep-links to an instance's traces (the run waterfalls the CLI's
 * `swarmkit trace` shows). Renders nothing when Jaeger isn't configured on the panel or the
 * instance's service (workspace id) is unknown. */
export function JaegerLink({
	baseUrl,
	service,
}: {
	baseUrl: string;
	service: string;
}) {
	const url = jaegerServiceUrl(baseUrl, service);
	if (!url) return null;
	return (
		<Button asChild variant="outline" size="sm">
			<a href={url} target="_blank" rel="noreferrer">
				<Waypoints />
				View in Jaeger
				<ExternalLink className="size-3" />
			</a>
		</Button>
	);
}
