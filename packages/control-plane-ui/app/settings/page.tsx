"use client";

import { ExternalLink } from "lucide-react";
import { useCallback } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Config } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

function Row({
	label,
	children,
}: { label: string; children: React.ReactNode }) {
	return (
		<div className="flex items-start justify-between gap-4 border-b py-2 last:border-0">
			<span className="text-sm text-muted-foreground">{label}</span>
			<span className="text-right text-sm">{children}</span>
		</div>
	);
}

function OnOff({ on }: { on: boolean }) {
	return (
		<Badge variant={on ? "success" : "muted"}>
			{on ? "enabled" : "disabled"}
		</Badge>
	);
}

function Mono({ children }: { children: React.ReactNode }) {
	return <span className="font-mono text-xs">{children}</span>;
}

function ExtLink({ href }: { href: string }) {
	return (
		<a
			href={href}
			target="_blank"
			rel="noreferrer"
			className="inline-flex items-center gap-1 font-mono text-xs text-foreground hover:underline"
		>
			{href}
			<ExternalLink className="size-3" />
		</a>
	);
}

export default function SettingsPage() {
	const fetcher = useCallback(() => api.config(), []);
	const { data, error, loading } = usePoll<Config>(fetcher, 30_000);

	const uiApi = process.env.NEXT_PUBLIC_CONTROL_PLANE_API || "(same-origin)";
	const uiOidc = process.env.NEXT_PUBLIC_OIDC_AUTHORITY ?? "";

	return (
		<>
			<PageHeader
				title="Settings"
				description="Read-only view of the panel and UI configuration."
			/>
			<div className="grid max-w-3xl gap-6 p-6">
				<Card>
					<CardHeader>
						<CardTitle>Panel</CardTitle>
					</CardHeader>
					<CardContent className="py-0">
						<Row label="swarmkit-control-plane">
							<Mono>{loading ? "…" : (data?.version ?? "unknown")}</Mono>
						</Row>
						<Row label="UI → panel API base">
							<Mono>{uiApi}</Mono>
						</Row>
						{error ? (
							<Row label="Status">
								<span className="text-destructive">unreachable: {error}</span>
							</Row>
						) : null}
					</CardContent>
				</Card>

				<Card>
					<CardHeader>
						<CardTitle>Authentication</CardTitle>
					</CardHeader>
					<CardContent className="py-0">
						<Row label="Operator token auth">
							<OnOff on={!!data?.auth.operator_tokens} />
						</Row>
						<Row label="OIDC (panel verifies)">
							<OnOff on={!!data?.auth.oidc.enabled} />
						</Row>
						{data?.auth.oidc.enabled ? (
							<>
								<Row label="OIDC issuer">
									<Mono>{data.auth.oidc.issuer}</Mono>
								</Row>
								<Row label="OIDC audience">
									<Mono>{data.auth.oidc.audience}</Mono>
								</Row>
							</>
						) : null}
						<Row label="UI login (OIDC)">
							{uiOidc ? <Mono>{uiOidc}</Mono> : <OnOff on={false} />}
						</Row>
						{!data?.auth.operator_tokens && !data?.auth.oidc.enabled ? (
							<Row label="">
								<span className="text-xs text-warning">
									Panel is unauthenticated (open mode).
								</span>
							</Row>
						) : null}
					</CardContent>
				</Card>

				<Card>
					<CardHeader>
						<CardTitle>Observability</CardTitle>
					</CardHeader>
					<CardContent className="py-0">
						<Row label="OTLP collector">
							{data?.observability.collector_endpoint ? (
								<Mono>{data.observability.collector_endpoint}</Mono>
							) : (
								<span className="text-xs text-muted-foreground">
									not configured
								</span>
							)}
						</Row>
						<Row label="Jaeger (traces)">
							{data?.observability.jaeger_url ? (
								<ExtLink href={data.observability.jaeger_url} />
							) : (
								<span className="text-xs text-muted-foreground">
									not configured
								</span>
							)}
						</Row>
						<Row label="Grafana (metrics)">
							{data?.observability.grafana_url ? (
								<ExtLink href={data.observability.grafana_url} />
							) : (
								<span className="text-xs text-muted-foreground">
									not configured
								</span>
							)}
						</Row>
					</CardContent>
				</Card>

				<Card>
					<CardHeader>
						<CardTitle>CORS origins</CardTitle>
					</CardHeader>
					<CardContent>
						{data && data.cors_origins.length > 0 ? (
							<div className="flex flex-wrap gap-2">
								{data.cors_origins.map((o) => (
									<Badge key={o} variant="secondary">
										{o}
									</Badge>
								))}
							</div>
						) : (
							<span className="text-sm text-muted-foreground">
								None configured (cross-origin browser calls are blocked).
							</span>
						)}
					</CardContent>
				</Card>
			</div>
		</>
	);
}
