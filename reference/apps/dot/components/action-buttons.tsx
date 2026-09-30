"use client";

import type { SuggestedAction } from "@/lib/types";

const LABEL: Record<SuggestedAction, string> = {
	reply: "Reply",
	draft_reply: "Draft reply",
	archive: "Archive",
	attend: "Attend",
	decline: "Decline",
	prep: "Prep",
	retrieve_context: "Retrieve context",
};

// Scaffold. Commit 5 wires each button to POST /api/items/:id/handle and streams the result via
// SSE into a ResultPanel. For commit 3 (static) the buttons render but do not act.
export function ActionButtons({
	suggested,
	peers,
}: {
	suggested: SuggestedAction;
	peers: SuggestedAction[];
}) {
	const items = [suggested, ...peers.filter((a) => a !== suggested)].slice(
		0,
		5,
	);
	return (
		<div className="flex flex-wrap gap-2">
			{items.map((action, i) => (
				<button
					key={action}
					type="button"
					className={
						i === 0
							? "inline-flex min-h-11 items-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90"
							: "inline-flex min-h-11 items-center rounded-md border border-input bg-background px-3 text-sm font-medium hover:bg-accent"
					}
					aria-label={LABEL[action]}
					disabled
				>
					{LABEL[action]}
				</button>
			))}
		</div>
	);
}
