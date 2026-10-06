"use client";

/**
 * Author — on-ramp to the bundled SwarmKit authoring agent.
 *
 * The portal has /chat for arbitrary topology conversations and /composer for direct
 * YAML editing (schema-driven form + canvas). This page is the third surface:
 * pick a mode, click Start, and the bundled swarmkit:author:<mode> topology from
 * #1045 is pointed at THIS workspace through a fresh conversation in /chat.
 *
 * Deliberately thin: it does not reimplement the chat UI, it opens one. Advanced
 * editing stays on /composer; this page is for the "I want X and I don't want to
 * think about YAML" entry point.
 */

import {
	BookTemplate,
	FileCode,
	GitBranch,
	Puzzle,
	Server,
	type Workflow,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Card } from "@/components/card";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

type Mode = "topology" | "skill" | "archetype" | "mcp-server" | "init";

const MODES: {
	id: Mode;
	label: string;
	blurb: string;
	Icon: typeof Workflow;
}[] = [
	{
		id: "topology",
		label: "Topology",
		blurb: "Create a new topology — an agent graph that returns something.",
		Icon: GitBranch,
	},
	{
		id: "skill",
		label: "Skill",
		blurb:
			"Add a capability — MCP tool wrapper, LLM prompt, decision, or composed.",
		Icon: Puzzle,
	},
	{
		id: "archetype",
		label: "Archetype",
		blurb: "Define a reusable agent configuration (prompt, model, skills).",
		Icon: BookTemplate,
	},
	{
		id: "mcp-server",
		label: "MCP server",
		blurb: "Register an MCP server — bundle, stdio, or remote http endpoint.",
		Icon: Server,
	},
	{
		id: "init",
		label: "Initialise workspace",
		blurb: "Scaffold a brand-new workspace from scratch.",
		Icon: FileCode,
	},
];

export default function AuthorPage() {
	const router = useRouter();
	const [exposed, setExposed] = useState<boolean | null>(null);
	const [starting, setStarting] = useState<Mode | null>(null);
	const [error, setError] = useState<string | null>(null);

	// Decide whether to render by asking GET /topologies — the authoring namespace
	// surfaces there when authoring.expose is on (PR 6 / PR 10 of #1045).
	useEffect(() => {
		let cancelled = false;
		api
			.topologies()
			.then((list) => {
				if (cancelled) return;
				setExposed(list.some((t) => t.startsWith("swarmkit:author:")));
			})
			.catch(() => !cancelled && setExposed(false));
		return () => {
			cancelled = true;
		};
	}, []);

	const start = useCallback(
		async (mode: Mode) => {
			setStarting(mode);
			setError(null);
			try {
				const { id } = await api.createConversation(`swarmkit:author:${mode}`);
				// Hand off to /chat, which already renders the new conversation at the top
				// of its sidebar — no new UI to maintain here.
				router.push(`/chat?conversation=${encodeURIComponent(id)}`);
			} catch (e) {
				setError(e instanceof Error ? e.message : "could not start the author");
				setStarting(null);
			}
		},
		[router],
	);

	if (exposed === null) {
		return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
	}
	if (!exposed) {
		return (
			<div className="mx-auto max-w-xl p-6">
				<Card>
					<div className="p-6 space-y-3">
						<h2 className="text-lg font-medium">
							Authoring is not exposed on this workspace
						</h2>
						<p className="text-sm text-muted-foreground">
							The bundled SwarmKit author lets anyone with chat access propose
							writes to this workspace through the IAM-scoped write-file skill.
							Opt in explicitly by setting <code>authoring.expose: true</code>{" "}
							on <code>workspace.yaml</code>, or
							<code>SWARMKIT_AUTHOR_EXPOSE=1</code> in the serve process
							environment.
						</p>
						<p className="text-sm text-muted-foreground">
							Direct YAML editing stays available on the{" "}
							<a className="underline" href="/composer">
								Composer
							</a>{" "}
							page.
						</p>
					</div>
				</Card>
			</div>
		);
	}

	return (
		<div className="mx-auto max-w-3xl p-6 space-y-6">
			<header className="space-y-2">
				<h1 className="text-2xl font-semibold">Author</h1>
				<p className="text-sm text-muted-foreground">
					Describe what you want in plain language — the bundled SwarmKit author
					generates the YAML and lands it in this workspace. For hand-editing,
					use the{" "}
					<a className="underline" href="/composer">
						Composer
					</a>
					.
				</p>
			</header>

			<div className="grid gap-3 sm:grid-cols-2">
				{MODES.map(({ id, label, blurb, Icon }) => (
					<Card key={id}>
						<button
							type="button"
							onClick={() => start(id)}
							disabled={starting !== null}
							className="w-full p-4 text-left transition hover:bg-muted/50 disabled:opacity-60"
						>
							<div className="flex items-start gap-3">
								<Icon
									size={20}
									className="mt-0.5 shrink-0 text-muted-foreground"
								/>
								<div className="space-y-1">
									<div className="font-medium">
										{label}
										{starting === id && (
											<span className="ml-2 text-xs text-muted-foreground">
												starting…
											</span>
										)}
									</div>
									<div className="text-xs text-muted-foreground">{blurb}</div>
								</div>
							</div>
						</button>
					</Card>
				))}
			</div>

			{error && (
				<div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
					{error}
				</div>
			)}
		</div>
	);
}
