"use client";

import { BarChart3, ExternalLink, Waypoints } from "lucide-react";
import { useCallback } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Observability } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

export function ObservabilityCard() {
	const fetcher = useCallback(() => api.observability(), []);
	const { data } = useResource<Observability>("/observability", fetcher, {
		refreshInterval: 30_000,
	});
	const obs = data ?? {
		collector_endpoint: "",
		jaeger_url: "",
		grafana_url: "",
	};
	const configured =
		obs.jaeger_url || obs.grafana_url || obs.collector_endpoint;

	return (
		<Card>
			<CardHeader>
				<CardTitle>Observability</CardTitle>
			</CardHeader>
			<CardContent className="space-y-3">
				{configured ? (
					<>
						<div className="flex flex-wrap gap-2">
							{obs.jaeger_url ? (
								<Button asChild variant="outline" size="sm">
									<a href={obs.jaeger_url} target="_blank" rel="noreferrer">
										<Waypoints />
										Traces (Jaeger)
										<ExternalLink className="size-3" />
									</a>
								</Button>
							) : null}
							{obs.grafana_url ? (
								<Button asChild variant="outline" size="sm">
									<a href={obs.grafana_url} target="_blank" rel="noreferrer">
										<BarChart3 />
										Metrics (Grafana)
										<ExternalLink className="size-3" />
									</a>
								</Button>
							) : null}
						</div>
						{obs.collector_endpoint ? (
							<p className="text-xs text-muted-foreground">
								Instances send OTLP to{" "}
								<code className="rounded bg-muted px-1 py-0.5">
									{obs.collector_endpoint}
								</code>
							</p>
						) : null}
					</>
				) : (
					<p className="text-sm text-muted-foreground">
						No dashboards configured. Start the bundle in{" "}
						<code className="rounded bg-muted px-1 py-0.5 text-xs">
							deploy/observability/
						</code>{" "}
						and set the panel's{" "}
						<code className="rounded bg-muted px-1 py-0.5 text-xs">
							--jaeger-url
						</code>{" "}
						/{" "}
						<code className="rounded bg-muted px-1 py-0.5 text-xs">
							--grafana-url
						</code>
						.
					</p>
				)}
			</CardContent>
		</Card>
	);
}
