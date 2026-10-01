import { fetchTodaysBrief } from "@/lib/brief";
import type { SwarmKitClient } from "@/lib/swarmkit-client";
import { describe, expect, it, vi } from "vitest";

function fakeClient(
	gmailPayload: unknown,
	calendarPayload: unknown,
): SwarmKitClient {
	const invokeMcpTool = vi
		.fn()
		.mockImplementation(async (serverId: string) =>
			serverId === "gmail" ? gmailPayload : calendarPayload,
		);
	return { invokeMcpTool } as unknown as SwarmKitClient;
}

describe("fetchTodaysBrief", () => {
	it("orders calendar first, then gmail, and reassigns ranks", async () => {
		const client = fakeClient(
			{
				threads: [
					{
						id: "t1",
						subject: "Hi",
						snippet: "hi",
						from: "a@x",
						receivedAt: "2026-09-30T05:00:00Z",
						labels: ["INBOX"],
					},
				],
			},
			{
				events: [
					{
						id: "e1",
						summary: "Standup",
						start: "2026-09-30T09:00:00Z",
						end: "2026-09-30T09:15:00Z",
					},
				],
			},
		);
		const brief = await fetchTodaysBrief(client);
		expect(brief.items).toHaveLength(2);
		expect(brief.items[0]?.source).toBe("calendar");
		expect(brief.items[1]?.source).toBe("gmail");
		expect(brief.items.map((i) => i.rank)).toEqual([1, 2]);
	});

	it("caps at 8 items", async () => {
		const threads = Array.from({ length: 12 }, (_, i) => ({
			id: `t${i}`,
			subject: `Item ${i}`,
			snippet: "x",
			from: "a@x",
			receivedAt: `2026-09-30T05:${String(i).padStart(2, "0")}:00Z`,
			labels: ["INBOX"],
		}));
		const client = fakeClient({ threads }, { events: [] });
		const brief = await fetchTodaysBrief(client);
		expect(brief.items).toHaveLength(8);
	});

	it("picks archive for CATEGORY_PROMOTIONS threads", async () => {
		const client = fakeClient(
			{
				threads: [
					{
						id: "t1",
						subject: "Newsletter",
						snippet: "x",
						from: "n@x",
						receivedAt: "2026-09-30T05:00:00Z",
						labels: ["INBOX", "CATEGORY_PROMOTIONS"],
					},
				],
			},
			{ events: [] },
		);
		const brief = await fetchTodaysBrief(client);
		expect(brief.items[0]?.suggestedAction).toBe("archive");
	});
});
