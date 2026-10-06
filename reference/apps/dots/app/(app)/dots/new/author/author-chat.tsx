"use client";

import { HttpAgent } from "@ag-ui/client";
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

// The Dot Author chat: a conversational on-ramp to the "author" topology in the shared
// SwarmKit workspace. The agent elicits what the user wants, delegates topology YAML writing
// to the bundled swarmkit:author:topology via its `author-topology` skill (#1055), and lands
// the Dot by invoking `create-dot`, which POSTs to /api/dots/intake (handled server-side).
//
// After the agent writes the Dot, this component polls /api/dots to detect the new entry and
// redirects to /dots/<id>. We watch the Dot list instead of parsing the SSE stream because
// CopilotChat does not surface tool-call results to the client the way raw AG-UI does.

export function AuthorChat({ existingIds }: { existingIds: string[] }): React.ReactElement {
	const router = useRouter();
	const previousIds = useRef<Set<string>>(new Set(existingIds));
	const [status, setStatus] = useState<string | null>(null);

	// Dedicated HttpAgent pointed at the Dot Author topology. threadId stays stable for the
	// life of the component so the LangGraph checkpointer resumes across turns.
	const agent = useMemo(
		() =>
			new HttpAgent({
				agentId: "default",
				url: "/api/ag-ui/author",
				threadId: `author:${crypto.randomUUID()}`,
			}),
		[],
	);

	// Watch the Dot list every 2s; when a new id appears, we assume the author just landed it
	// and navigate. 2s is a compromise — the author takes 30–120s per session, and the user is
	// already waiting on chat responses, so this isn't adding noticeable polling pressure.
	useEffect(() => {
		let cancelled = false;
		const check = async (): Promise<void> => {
			if (cancelled) return;
			try {
				const res = await fetch("/api/dots", { cache: "no-store" });
				if (!res.ok) return;
				const body = (await res.json()) as { dots?: { id: string }[] };
				const current = new Set(body.dots?.map((d) => d.id) ?? []);
				for (const id of current) {
					if (!previousIds.current.has(id)) {
						setStatus(`Opening ${id}…`);
						router.push(`/dots/${id}`);
						router.refresh();
						cancelled = true;
						return;
					}
				}
				previousIds.current = current;
			} catch {
				// Network blip — keep polling. Not fatal.
			}
		};
		const interval = setInterval(check, 2000);
		return () => {
			cancelled = true;
			clearInterval(interval);
		};
	}, [router]);

	return (
		<CopilotKit selfManagedAgents={{ default: agent }} showDevConsole={false}>
			<div className="flex min-h-0 flex-1 flex-col">
				{status ? (
					<div className="border-b border-border bg-muted/40 px-6 py-2 text-sm text-muted-foreground">
						{status}
					</div>
				) : null}
				<CopilotChat
					instructions="You are the Dot Author."
					labels={{
						title: "Describe your Dot",
						initial:
							"What should the new coworker do? Tell me the goal, what you'll send it, and what you want back.",
					}}
				/>
			</div>
		</CopilotKit>
	);
}
