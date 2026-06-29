"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import type { ArtifactVersion } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { cn } from "@/lib/utils";

export default function ArtifactDetailPage() {
	const params = useParams();
	const kind = String(params.kind);
	const id = decodeURIComponent(String(params.id));

	const fetcher = useCallback(() => api.artifactVersions(kind, id), [kind, id]);
	const { data, error, loading } = usePoll<ArtifactVersion[]>(fetcher, 15_000);
	const versions = data ?? [];

	const [selected, setSelected] = useState<string | null>(null);
	const [content, setContent] = useState<ArtifactVersion | null>(null);

	// Default to the latest version once loaded.
	const active = selected ?? versions[0]?.version ?? null;

	useEffect(() => {
		if (!active) return;
		let cancelled = false;
		api.artifactVersion(kind, id, active).then(
			(v) => {
				if (!cancelled) setContent(v);
			},
			() => {},
		);
		return () => {
			cancelled = true;
		};
	}, [kind, id, active]);

	return (
		<>
			<PageHeader
				title={id}
				description={`${kind} · ${versions.length} version(s)`}
			/>
			<div className="space-y-6 p-6">
				<Link
					href="/artifacts"
					className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
				>
					<ArrowLeft className="size-4" />
					All artifacts
				</Link>

				{error ? (
					<p className="text-sm text-destructive">Could not load: {error}</p>
				) : null}
				{loading && versions.length === 0 ? (
					<p className="text-sm text-muted-foreground">Loading…</p>
				) : null}

				<Card>
					<CardHeader>
						<CardTitle>Version history</CardTitle>
					</CardHeader>
					<CardContent className="p-0">
						<Table>
							<TableHeader>
								<TableRow>
									<TableHead>Version</TableHead>
									<TableHead>Author</TableHead>
									<TableHead>Created</TableHead>
									<TableHead>Schema</TableHead>
									<TableHead>Hash</TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{versions.map((v) => (
									<TableRow
										key={v.version}
										onClick={() => setSelected(v.version)}
										className={cn(
											"cursor-pointer",
											v.version === active ? "bg-accent" : "",
										)}
									>
										<TableCell className="font-mono text-xs font-medium">
											{v.version}
											{v.version === versions[0]?.version ? (
												<Badge variant="secondary" className="ml-2">
													latest
												</Badge>
											) : null}
										</TableCell>
										<TableCell className="text-muted-foreground">
											{v.authored_by || "—"}
										</TableCell>
										<TableCell className="text-xs text-muted-foreground">
											{v.created_at}
										</TableCell>
										<TableCell className="text-muted-foreground">
											{v.schema_version || "—"}
										</TableCell>
										<TableCell className="font-mono text-xs text-muted-foreground">
											{v.content_hash.slice(0, 12)}
										</TableCell>
									</TableRow>
								))}
							</TableBody>
						</Table>
					</CardContent>
				</Card>

				{content ? (
					<Card>
						<CardHeader>
							<CardTitle>
								Content{" "}
								<span className="font-mono text-sm text-muted-foreground">
									{content.version}
								</span>
							</CardTitle>
						</CardHeader>
						<CardContent>
							<pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
								{typeof content.content === "string"
									? content.content
									: JSON.stringify(content.content, null, 2)}
							</pre>
						</CardContent>
					</Card>
				) : null}
			</div>
		</>
	);
}
