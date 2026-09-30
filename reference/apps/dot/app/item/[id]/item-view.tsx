"use client";

import { ActionButtons } from "@/components/action-buttons";
import { type ChatMessage, ChatPane } from "@/components/chat-pane";
import { ResultPanel } from "@/components/result-panel";
import { SourceBadge } from "@/components/source-badge";
import type { Artefact, HandleRunEvent } from "@/lib/artefacts";
import { formatTimeShort, relativeShort } from "@/lib/format";
import { handleItem } from "@/lib/handle-client";
import type { Brief, Item, SuggestedAction } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

async function fetchBrief(): Promise<Brief> {
	const res = await fetch("/api/brief", { cache: "no-store" });
	if (!res.ok) throw new Error(`HTTP ${res.status}`);
	return (await res.json()) as Brief;
}

export function ItemView({ id }: { id: string }) {
	const { data: brief } = useQuery<Brief>({
		queryKey: ["brief"],
		queryFn: fetchBrief,
		staleTime: 15_000,
	});
	const item = brief?.items.find((i) => i.id === id) ?? null;

	const [messages, setMessages] = useState<ChatMessage[]>(() =>
		item
			? [{ from: "system", text: `Scoped to "${item.title}"` }]
			: [{ from: "system", text: "Scoped chat" }],
	);
	const [activeAction, setActiveAction] = useState<SuggestedAction | null>(
		null,
	);
	const [pending, setPending] = useState(false);
	const [progress, setProgress] = useState<string | null>(null);
	const [artefact, setArtefact] = useState<Artefact | null>(null);
	const [error, setError] = useState<string | null>(null);

	if (!brief) {
		return (
			<main className="mx-auto max-w-screen-md px-4 py-6 md:py-10">
				<div className="h-6 w-40 animate-pulse rounded bg-muted" />
			</main>
		);
	}
	if (!item) {
		return (
			<main className="mx-auto max-w-screen-md px-4 py-6 md:py-10">
				<Link href="/" className="text-sm underline">
					← Back to brief
				</Link>
				<p className="mt-6 text-sm text-muted-foreground">
					Item not found in today's brief. It may have expired — refresh the
					brief or run one now.
				</p>
			</main>
		);
	}
	const timing = item.scheduledFor
		? `${formatTimeShort(item.scheduledFor)} · ${relativeShort(item.scheduledFor)}`
		: item.receivedAt
			? relativeShort(item.receivedAt)
			: null;

	function onEvent(e: HandleRunEvent) {
		if (e.type === "progress") setProgress(e.message ?? null);
		if (e.type === "result" && e.artefact) {
			const finished = e.artefact;
			setArtefact(finished);
			setProgress(null);
			setPending(false);
			setMessages((m) => [
				...m,
				{ from: "assistant", text: describeArtefact(finished) },
			]);
		}
		if (e.type === "error") {
			setError(e.detail ?? "run failed");
			setProgress(null);
			setPending(false);
		}
	}

	const itemId = item.id;

	async function dispatchAction(action: SuggestedAction) {
		setActiveAction(action);
		setPending(true);
		setError(null);
		setProgress("Working…");
		setArtefact(null);
		setMessages((m) => [
			...m,
			{ from: "owner", text: `Requested: ${action.replaceAll("_", " ")}` },
		]);
		await handleItem(itemId, { suggestedAction: action }, onEvent, "handle");
	}

	async function sendChat(text: string) {
		setPending(true);
		setError(null);
		setProgress("Thinking…");
		setMessages((m) => [...m, { from: "owner", text }]);
		await handleItem(
			itemId,
			{ suggestedAction: "chat", userIntent: text },
			onEvent,
			"chat",
		);
	}

	return (
		<main className="mx-auto max-w-screen-lg px-4 py-6 md:py-10">
			<Link
				href="/"
				className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
			>
				<span aria-hidden>←</span> Back to brief
			</Link>
			<div className="mt-4 grid gap-6 md:grid-cols-[minmax(0,1fr)_360px]">
				<section className="space-y-4">
					<article className="rounded-lg border border-border bg-card p-4 shadow-sm">
						<div className="flex items-center justify-between gap-2">
							<SourceBadge source={item.source} />
							{timing && (
								<span className="text-xs text-muted-foreground">{timing}</span>
							)}
						</div>
						<h1 className="mt-2 text-lg font-semibold leading-snug md:text-xl">
							{item.title}
						</h1>
						<p className="mt-1 text-sm italic text-muted-foreground">
							{item.why}
						</p>
						<div className="mt-4">
							<ActionButtons
								suggested={item.suggestedAction}
								peers={item.peerActions}
								pending={pending}
								activeAction={activeAction}
								onDispatch={dispatchAction}
							/>
						</div>
					</article>
					<ResultPanel artefact={artefact} progress={progress} error={error} />
				</section>
				<aside className="min-h-[420px] rounded-lg border border-border bg-card shadow-sm md:sticky md:top-6 md:h-[calc(100vh-3rem)]">
					<ChatPane messages={messages} onSend={sendChat} pending={pending} />
				</aside>
			</div>
		</main>
	);
}

function describeArtefact(a: Artefact): string {
	switch (a.kind) {
		case "email_draft":
			return `Drafted a reply to ${a.to}: "${a.subject}"`;
		case "prep_note":
			return `Prepared: ${a.title}`;
		case "retrieved_thread":
			return `Found: ${a.title}`;
		case "acknowledgement":
			return a.summary;
	}
}
