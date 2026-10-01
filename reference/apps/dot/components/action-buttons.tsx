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

export function ActionButtons({
	suggested,
	peers,
	pending = false,
	activeAction = null,
	onDispatch,
}: {
	suggested: SuggestedAction;
	peers: SuggestedAction[];
	pending?: boolean;
	activeAction?: SuggestedAction | null;
	// When omitted the buttons are read-only (used on the brief-list card, where tapping the
	// card body navigates to /item/[id]).
	onDispatch?: (action: SuggestedAction) => void;
}) {
	const items = [suggested, ...peers.filter((a) => a !== suggested)].slice(
		0,
		5,
	);
	const interactive = Boolean(onDispatch);
	return (
		<div className="flex flex-wrap gap-2">
			{items.map((action, i) => {
				const isPrimary = i === 0;
				const isActive = activeAction === action;
				const className = isPrimary
					? "inline-flex min-h-11 items-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-60"
					: "inline-flex min-h-11 items-center rounded-md border border-input bg-background px-3 text-sm font-medium hover:bg-accent disabled:opacity-60";
				return (
					<button
						key={action}
						type="button"
						className={className}
						aria-label={LABEL[action]}
						disabled={pending || !interactive}
						onClick={interactive ? () => onDispatch?.(action) : undefined}
					>
						{isActive && pending ? `${LABEL[action]}…` : LABEL[action]}
					</button>
				);
			})}
		</div>
	);
}
