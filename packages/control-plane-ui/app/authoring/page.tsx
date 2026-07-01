"use client";

import { BotMessageSquare, Send, Sparkles } from "lucide-react";
import { useCallback, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, api } from "@/lib/api";
import type { DraftArtifact, Instance } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

const FIELD = "h-9 rounded-md border border-input bg-background px-2 text-sm";

interface Turn {
	role: "user" | "assistant";
	text: string;
	artifact?: DraftArtifact | null;
	proposed?: boolean;
}

export default function AuthoringPage() {
	const listInstances = useCallback(() => api.listInstances(), []);
	const { data: instances } = usePoll<Instance[]>(listInstances, 30_000);
	// Authoring drives a swarm on the instance's serve — Mode A (directly reachable) only.
	const reachable = (instances ?? []).filter((i) => i.connection === "direct");

	const [instanceId, setInstanceId] = useState("");
	const [topology, setTopology] = useState("authoring");
	const [input, setInput] = useState("");
	const [turns, setTurns] = useState<Turn[]>([]);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");

	const target = instanceId || reachable[0]?.id || "";

	async function send() {
		const message = input.trim();
		if (!message || !target || busy) return;
		setError("");
		setBusy(true);
		setInput("");
		setTurns((t) => [...t, { role: "user", text: message }]);
		try {
			const res = await api.author(target, message, topology);
			setTurns((t) => [
				...t,
				{ role: "assistant", text: res.reply, artifact: res.artifact },
			]);
		} catch (e) {
			const msg = e instanceof ApiError ? e.message : String(e);
			setError(msg);
			setTurns((t) => [
				...t,
				{ role: "assistant", text: "— the authoring run did not return." },
			]);
		} finally {
			setBusy(false);
		}
	}

	async function propose(idx: number, art: DraftArtifact) {
		try {
			await api.createProposal({
				kind: art.kind,
				artifact_id: art.id,
				content: art.content,
				proposed_by: "authoring",
				signal: "authoring",
			});
			setTurns((t) =>
				t.map((x, i) => (i === idx ? { ...x, proposed: true } : x)),
			);
		} catch (e) {
			setError(e instanceof ApiError ? e.message : String(e));
		}
	}

	return (
		<>
			<PageHeader
				title="Authoring"
				description="Draft a skill or topology by talking to an instance's authoring swarm, then propose it for approval."
			/>
			<div className="flex max-w-3xl flex-col gap-4 p-6">
				<div className="flex flex-wrap items-end gap-4">
					<div className="flex flex-col gap-1">
						<label htmlFor="instance" className="text-sm font-medium">
							Instance
						</label>
						<select
							id="instance"
							value={target}
							onChange={(e) => setInstanceId(e.target.value)}
							className={FIELD}
						>
							{reachable.length === 0 ? (
								<option value="">No Mode A instances</option>
							) : (
								reachable.map((i) => (
									<option key={i.id} value={i.id}>
										{i.name}
									</option>
								))
							)}
						</select>
					</div>
					<div className="flex flex-col gap-1">
						<label htmlFor="topology" className="text-sm font-medium">
							Authoring topology
						</label>
						<input
							id="topology"
							value={topology}
							onChange={(e) => setTopology(e.target.value)}
							className={`${FIELD} font-mono text-xs`}
						/>
					</div>
				</div>

				<Card className="min-h-80">
					<CardContent className="space-y-4 pt-6">
						{turns.length === 0 ? (
							<div className="flex flex-col items-center gap-2 py-12 text-center text-muted-foreground">
								<BotMessageSquare className="size-8" />
								<p className="text-sm">
									Describe the skill or topology you want. The authoring swarm
									drafts it; nothing is published until a human approves.
								</p>
							</div>
						) : (
							turns.map((t, i) => (
								<div
									key={`${i}-${t.role}`}
									className={t.role === "user" ? "text-right" : ""}
								>
									<div
										className={`inline-block max-w-[85%] whitespace-pre-wrap rounded-md px-3 py-2 text-left text-sm ${
											t.role === "user"
												? "bg-primary text-primary-foreground"
												: "bg-muted"
										}`}
									>
										{t.text}
									</div>
									{t.artifact ? (
										<div className="mt-2">
											<Card>
												<CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
													<CardTitle className="flex items-center gap-2 text-sm">
														<Sparkles className="size-4" />
														Drafted artifact
														<Badge variant="secondary">{t.artifact.kind}</Badge>
														<span className="font-mono text-xs">
															{t.artifact.id}
														</span>
													</CardTitle>
													{t.proposed ? (
														<Badge variant="success">proposed →</Badge>
													) : (
														<Button
															size="sm"
															onClick={() =>
																t.artifact && propose(i, t.artifact)
															}
														>
															Propose for approval
														</Button>
													)}
												</CardHeader>
												<CardContent>
													<pre className="max-h-64 overflow-auto rounded bg-muted p-3 font-mono text-xs">
														{JSON.stringify(t.artifact.content, null, 2)}
													</pre>
												</CardContent>
											</Card>
										</div>
									) : null}
								</div>
							))
						)}
					</CardContent>
				</Card>

				{error ? <p className="text-sm text-destructive">{error}</p> : null}

				<div className="flex items-end gap-2">
					<textarea
						value={input}
						onChange={(e) => setInput(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === "Enter" && !e.shiftKey) {
								e.preventDefault();
								send();
							}
						}}
						rows={2}
						placeholder={
							reachable.length === 0
								? "Enroll a Mode A instance to author…"
								: "Describe the skill or topology to draft…"
						}
						disabled={reachable.length === 0}
						className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm"
					/>
					<Button onClick={send} disabled={busy || !input.trim() || !target}>
						<Send className="size-4" />
						{busy ? "Drafting…" : "Send"}
					</Button>
				</div>
			</div>
		</>
	);
}
