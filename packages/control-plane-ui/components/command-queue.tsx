"use client";

import { useCallback, useState } from "react";

import { CommandStatusBadge } from "@/components/health-badge";
import { Button } from "@/components/ui/button";
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
import { type Command, KNOWN_VERBS } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

const TIER_RANK: Record<string, number> = { read: 0, run: 1, admin: 2 };

export function CommandQueue({
	instanceId,
	tier,
}: { instanceId: string; tier: string }) {
	const fetcher = useCallback(() => api.listCommands(instanceId), [instanceId]);
	const { data, error, refresh } = usePoll<Command[]>(fetcher, 3000);
	const commands = data ?? [];

	const [verb, setVerb] = useState("capabilities");
	const [argsText, setArgsText] = useState("");
	const [submitting, setSubmitting] = useState(false);
	const [formError, setFormError] = useState<string | null>(null);

	const grant = TIER_RANK[tier] ?? -1;

	async function enqueue() {
		setFormError(null);
		let args: Record<string, unknown> = {};
		if (argsText.trim()) {
			try {
				args = JSON.parse(argsText);
			} catch {
				setFormError("args must be valid JSON");
				return;
			}
		}
		setSubmitting(true);
		try {
			await api.enqueueCommand(instanceId, { verb, args });
			setArgsText("");
			refresh();
		} catch (err) {
			setFormError(err instanceof Error ? err.message : String(err));
		} finally {
			setSubmitting(false);
		}
	}

	return (
		<Card>
			<CardHeader>
				<CardTitle>Command queue</CardTitle>
			</CardHeader>
			<CardContent className="space-y-4">
				<div className="flex flex-wrap items-end gap-2">
					<div className="flex flex-col gap-1">
						<label htmlFor="verb" className="text-xs text-muted-foreground">
							Verb
						</label>
						<select
							id="verb"
							value={verb}
							onChange={(e) => setVerb(e.target.value)}
							className="h-9 rounded-md border border-input bg-background px-2 text-sm"
						>
							{KNOWN_VERBS.map((v) => {
								const overTier = (TIER_RANK[v.tier] ?? 99) > grant;
								return (
									<option key={v.verb} value={v.verb} disabled={overTier}>
										{v.verb} ({v.tier}){overTier ? " — over tier" : ""}
									</option>
								);
							})}
						</select>
					</div>
					<div className="flex flex-1 flex-col gap-1">
						<label htmlFor="args" className="text-xs text-muted-foreground">
							Args (JSON, optional)
						</label>
						<input
							id="args"
							value={argsText}
							onChange={(e) => setArgsText(e.target.value)}
							placeholder='{"topology_name": "hello", "body": {"input": "hi"}}'
							className="h-9 rounded-md border border-input bg-background px-2 font-mono text-xs"
						/>
					</div>
					<Button onClick={enqueue} disabled={submitting}>
						{submitting ? "Enqueuing…" : "Enqueue"}
					</Button>
				</div>
				{formError ? (
					<p className="text-sm text-destructive">{formError}</p>
				) : null}
				{error ? <p className="text-sm text-destructive">{error}</p> : null}

				{commands.length === 0 ? (
					<p className="text-sm text-muted-foreground">No commands yet.</p>
				) : (
					<Table>
						<TableHeader>
							<TableRow>
								<TableHead>Verb</TableHead>
								<TableHead>Status</TableHead>
								<TableHead>Created</TableHead>
								<TableHead>Result</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{commands.map((c) => (
								<TableRow key={c.cmd_id}>
									<TableCell className="font-medium">{c.verb}</TableCell>
									<TableCell>
										<CommandStatusBadge status={c.status} />
									</TableCell>
									<TableCell className="text-xs text-muted-foreground">
										{c.created_at}
									</TableCell>
									<TableCell className="max-w-md truncate font-mono text-xs text-muted-foreground">
										{c.error ?? (c.output ? JSON.stringify(c.output) : "—")}
									</TableCell>
								</TableRow>
							))}
						</TableBody>
					</Table>
				)}
			</CardContent>
		</Card>
	);
}
