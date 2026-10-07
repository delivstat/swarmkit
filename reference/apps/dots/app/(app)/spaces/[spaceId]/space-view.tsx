"use client";

import { HttpAgent } from "@ag-ui/client";
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";
import { useEffect, useMemo, useState } from "react";

import type { Dot } from "@/lib/dots.config";
import type { Space } from "@/lib/spaces.config";

// Space view — three regions:
//  - top: Space header (name, description, correlation id)
//  - left: activity timeline (jobs filtered by correlation_id)
//  - right: chat panel with a Dot picker (sends ?spaceId= on every AG-UI request)
// All runs the chat starts carry correlation_id = space:<id>, so the activity feed
// shows the full cross-Dot timeline.

interface ActivityJob {
	job_id: string;
	topology: string;
	status: string;
	created_at: string;
	completed_at: string | null;
	source: string | null;
	correlation_id: string | null;
}

export function SpaceView({
	space,
	dots,
}: {
	space: Space;
	dots: Dot[];
}): React.ReactElement {
	// Dot picker — defaults to the first non-author Dot so a fresh Space opens on a
	// coworker rather than the Dot-authoring surface. The user can switch any time.
	const [dotId, setDotId] = useState<string>(() => {
		const nonAuthor = dots.find((d) => d.id !== "author");
		return (nonAuthor ?? dots[0])?.id ?? "";
	});
	const activeDot = dots.find((d) => d.id === dotId);

	// One HttpAgent per (Dot, Space) pair. threadId stays stable so the LangGraph
	// checkpointer resumes within the Dot's conversation; correlation_id (set server-side
	// via ?spaceId=) groups across Dots in the Space.
	const agent = useMemo(() => {
		if (!activeDot) return null;
		return new HttpAgent({
			agentId: "default",
			url: `/api/ag-ui?dotId=${encodeURIComponent(activeDot.id)}&spaceId=${encodeURIComponent(space.id)}`,
			threadId: `space:${space.id}:dot:${activeDot.id}`,
		});
	}, [activeDot, space.id]);

	// Activity poll — pulls /jobs/history for this Space's correlation_id every 5s.
	const [activity, setActivity] = useState<ActivityJob[] | null>(null);
	useEffect(() => {
		let cancelled = false;
		const fetchActivity = async (): Promise<void> => {
			try {
				const res = await fetch(`/api/spaces/${space.id}/activity`, {
					cache: "no-store",
				});
				if (!res.ok || cancelled) return;
				const body = (await res.json()) as { jobs: ActivityJob[] };
				if (!cancelled) setActivity(body.jobs);
			} catch {
				/* transient network error — try again next tick */
			}
		};
		fetchActivity();
		const interval = setInterval(fetchActivity, 5000);
		return () => {
			cancelled = true;
			clearInterval(interval);
		};
	}, [space.id]);

	return (
		<div className="flex min-h-0 flex-1 flex-col">
			<header className="border-b border-border px-6 py-4">
				<div className="flex items-baseline justify-between">
					<h1 className="text-lg font-semibold">{space.name}</h1>
					<span className="text-xs text-muted-foreground">
						correlation_id: <code>space:{space.id}</code>
					</span>
				</div>
				{space.description ? (
					<p className="mt-1 text-sm text-muted-foreground">{space.description}</p>
				) : null}
			</header>

			<div className="flex min-h-0 flex-1">
				{/* Left: Activity + Artifacts/Approvals (artifacts/approvals wired as follow-ups). */}
				<aside className="w-80 shrink-0 overflow-y-auto border-r border-border bg-muted/20 p-4">
					<h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
						Activity
					</h2>
					{activity === null ? (
						<p className="mt-3 text-xs text-muted-foreground">Loading…</p>
					) : activity.length === 0 ? (
						<p className="mt-3 text-xs text-muted-foreground">
							No runs in this Space yet. Chat with a Dot on the right to start one.
						</p>
					) : (
						<ul className="mt-3 space-y-2">
							{activity.map((j) => (
								<li
									key={j.job_id}
									className="rounded border border-border bg-background p-3 text-xs"
								>
									<div className="font-medium">{j.topology}</div>
									<div className="mt-0.5 text-muted-foreground">
										{j.status} · {new Date(j.created_at).toLocaleTimeString()}
									</div>
									<div className="mt-1 truncate text-[10px] text-muted-foreground" title={j.job_id}>
										job {j.job_id.slice(0, 8)}
									</div>
								</li>
							))}
						</ul>
					)}

					<h2 className="mt-6 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
						Artifacts
					</h2>
					<p className="mt-2 text-xs text-muted-foreground">
						Dot writes under <code>spaces/{space.id}/</code> land here. Rail wired as a follow-up
						when the demo workspace emits them.
					</p>

					<h2 className="mt-6 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
						Pending approvals
					</h2>
					<p className="mt-2 text-xs text-muted-foreground">
						HITL approvals scoped to this Space render here. Follow-up PR.
					</p>
				</aside>

				{/* Right: chat with Dot picker. */}
				<section className="flex min-h-0 flex-1 flex-col">
					<div className="flex items-center gap-3 border-b border-border px-4 py-2">
						<label
							htmlFor="dot-picker"
							className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
						>
							Active Dot
						</label>
						<select
							id="dot-picker"
							value={dotId}
							onChange={(e) => setDotId(e.target.value)}
							className="rounded border border-border bg-background px-2 py-1 text-sm"
						>
							{dots.map((d) => (
								<option key={d.id} value={d.id}>
									{d.name}
								</option>
							))}
						</select>
						{activeDot ? (
							<span className="text-xs text-muted-foreground">— {activeDot.role}</span>
						) : null}
					</div>
					<div className="flex min-h-0 flex-1 flex-col">
						{agent && activeDot ? (
							<CopilotKit
								key={`${space.id}:${activeDot.id}`}
								selfManagedAgents={{ default: agent }}
								showDevConsole={false}
							>
								<CopilotChat
									instructions={`${activeDot.role}. You are chatting inside the "${space.name}" Space; artifacts and prior turns from any Dot in this Space share one correlation_id.`}
									labels={{
										title: activeDot.name,
										initial: activeDot.greeting,
									}}
								/>
							</CopilotKit>
						) : (
							<div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
								No Dots configured.
							</div>
						)}
					</div>
				</section>
			</div>
		</div>
	);
}
