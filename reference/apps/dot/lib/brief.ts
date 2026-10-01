// Fast-lane brief composition: two MCP invocations (Gmail search + Calendar list) into a
// ranked Brief. Zero LLM tokens (design/details/dot-app.md §Two data-fetch lanes).
//
// The ranking here is heuristic and deliberately simple: calendar events soonest, then unread
// email threads by recency. Later commits swap this with the workspace's item-ranker archetype
// running against the same fetched pool.

import type { SwarmKitClient } from "./swarmkit-client";
import type { Brief, Item, SuggestedAction } from "./types";

interface GmailThread {
	id: string;
	subject: string;
	snippet: string;
	from: string;
	receivedAt: string;
	labels: string[];
}

interface CalendarEvent {
	id: string;
	summary: string;
	description?: string;
	start: string;
	end: string;
	attendees?: number;
}

interface GmailSearchResult {
	threads: GmailThread[];
}
interface CalendarListResult {
	events: CalendarEvent[];
}

export async function fetchTodaysBrief(client: SwarmKitClient): Promise<Brief> {
	const now = new Date();
	const dayEnd = new Date(now);
	dayEnd.setUTCHours(23, 59, 59, 999);
	const tomorrowEnd = new Date(dayEnd.getTime() + 86_400_000);

	const [gmail, calendar] = await Promise.all([
		client.invokeMcpTool<GmailSearchResult>("gmail", "search_threads", {
			query: "is:unread in:inbox newer_than:1d",
			limit: 20,
		}),
		client.invokeMcpTool<CalendarListResult>("google-calendar", "list_events", {
			from: now.toISOString(),
			to: tomorrowEnd.toISOString(),
			limit: 20,
		}),
	]);

	const items: Item[] = [
		...calendar.events.map(eventToItem),
		...gmail.threads.map(threadToItem),
	];
	items.sort(rankItems).forEach((item, i) => {
		item.rank = i + 1;
	});
	return {
		runId: `brief-${now.toISOString().slice(0, 10)}-${now.getTime()}`,
		generatedAt: now.toISOString(),
		items: items.slice(0, 8),
	};
}

function eventToItem(e: CalendarEvent): Item {
	const suggested: SuggestedAction = e.description ? "attend" : "prep";
	return {
		id: `cal-${e.id}`,
		source: "calendar",
		title: `${e.summary}${e.attendees ? ` · ${e.attendees} attendees` : ""}`,
		why: e.description
			? `Starts ${humanIn(e.start)}. ${trim(e.description, 80)}`
			: `Starts ${humanIn(e.start)}. Agenda is empty — decide whether to keep it.`,
		rank: 0,
		suggestedAction: suggested,
		peerActions: suggested === "prep" ? ["decline"] : ["prep", "decline"],
		scheduledFor: e.start,
	};
}

function threadToItem(t: GmailThread): Item {
	const isNewsletter = t.labels.includes("CATEGORY_PROMOTIONS");
	const suggested: SuggestedAction = isNewsletter ? "archive" : "draft_reply";
	return {
		id: `mail-${t.id}`,
		source: "gmail",
		title: t.subject,
		why: `${t.from}. ${trim(t.snippet, 100)}`,
		rank: 0,
		suggestedAction: suggested,
		peerActions: isNewsletter
			? ["retrieve_context"]
			: ["retrieve_context", "archive"],
		receivedAt: t.receivedAt,
	};
}

function rankItems(a: Item, b: Item): number {
	const at = a.scheduledFor ?? a.receivedAt ?? "";
	const bt = b.scheduledFor ?? b.receivedAt ?? "";
	if (a.source !== b.source) return a.source === "calendar" ? -1 : 1;
	return a.source === "calendar" ? at.localeCompare(bt) : bt.localeCompare(at);
}

function humanIn(iso: string, now = new Date()): string {
	const mins = Math.round((new Date(iso).getTime() - now.getTime()) / 60_000);
	if (mins < 60) return `in ${Math.max(mins, 0)}m`;
	const hours = Math.round(mins / 60);
	if (hours < 24) return `in ${hours}h`;
	return `in ${Math.round(hours / 24)}d`;
}

function trim(s: string, n: number): string {
	return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
