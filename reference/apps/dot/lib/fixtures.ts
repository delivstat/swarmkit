// Fixture used until commit 4 wires /api/brief to a real morning-brief run. Shape matches the
// morning-brief topology's output_schema; refreshing this file is safe (it's read at render
// time, no snapshot tests).

import type { Brief, HistoryEntry, Item } from "./types";

const now = new Date("2026-09-30T07:15:00Z");

function ago(days: number): string {
	const d = new Date(now.getTime() - days * 86_400_000);
	return d.toISOString();
}

export const staticBrief: Brief = {
	runId: "brief-2026-09-30",
	generatedAt: now.toISOString(),
	items: [
		{
			id: "i1",
			source: "calendar",
			title: "Sync with the Minder OEM partner — 10:00–10:30",
			why: "Starts in 2 h 45 m. You have no prep note yet and the doc from last week hasn't been read.",
			rank: 1,
			suggestedAction: "prep",
			peerActions: ["retrieve_context"],
			scheduledFor: "2026-09-30T10:00:00Z",
		},
		{
			id: "i2",
			source: "gmail",
			title: "Re: DOT app pilot — cost breakdown",
			why: "Two people are waiting on a numbers reply. You already have last month's usage handy.",
			rank: 2,
			suggestedAction: "draft_reply",
			peerActions: ["retrieve_context", "archive"],
			receivedAt: ago(0.3),
		},
		{
			id: "i3",
			source: "calendar",
			title: "Q4 planning — 15:30–17:00",
			why: "3 hour block; agenda is empty. Worth deciding whether it stays on the calendar.",
			rank: 3,
			suggestedAction: "prep",
			peerActions: ["decline"],
			scheduledFor: "2026-09-30T15:30:00Z",
		},
		{
			id: "i4",
			source: "gmail",
			title: "Invoice #2384 approved — auto-charge on Oct 3",
			why: "Financial confirmation; no action beyond acknowledging.",
			rank: 4,
			suggestedAction: "archive",
			peerActions: [],
			receivedAt: ago(0.6),
		},
		{
			id: "i5",
			source: "gmail",
			title: "Newsletter: three papers on eval harnesses",
			why: "Long read. Skimmable but not urgent.",
			rank: 5,
			suggestedAction: "archive",
			peerActions: ["retrieve_context"],
			receivedAt: ago(0.9),
		},
	],
};

export const staticHistory: HistoryEntry[] = Array.from(
	{ length: 7 },
	(_, i) => ({
		runId: `brief-${i}`,
		generatedAt: ago(i + 1),
		itemCount: 5 - (i % 3),
	}),
);

export function orderedItems(brief: Brief): Item[] {
	return [...brief.items].sort((a, b) => a.rank - b.rank);
}
