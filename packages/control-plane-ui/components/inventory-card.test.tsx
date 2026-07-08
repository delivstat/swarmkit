import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { CachedState } from "@/lib/types";
import { InventoryCard } from "./inventory-card";

vi.mock("@/lib/api", () => ({
	api: {
		instanceState: vi.fn(),
		syncInstance: vi.fn(),
		adoptArtifact: vi.fn(),
	},
}));

const mockApi = vi.mocked(api);

const CACHED: CachedState = {
	synced_at: "2026-07-07T10:00:00Z",
	state: {
		apiVersion: "swarmkit/v1",
		kind: "InstanceState",
		workspace_id: "sterling-oms",
		schema_version: "1.7.0",
		artifacts: {
			topologies: [
				{
					id: "solution-design",
					version: "1.0.0",
					content_hash: "abcdef123456",
					content: { kind: "Topology" },
				},
			],
			skills: [
				{
					id: "get-weather",
					version: "1.0.0",
					content_hash: "deadbeef0000",
					content: { kind: "Skill" },
				},
			],
			archetypes: [],
			triggers: [],
		},
		providers: ["anthropic"],
		governance_provider: "mock",
		health: {},
	},
};

function renderCard() {
	return render(
		<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
			<InventoryCard instanceId="i1" />
		</SWRConfig>,
	);
}

describe("InventoryCard", () => {
	beforeEach(() => vi.clearAllMocks());

	it("prompts to sync when nothing is cached (404)", async () => {
		mockApi.instanceState.mockRejectedValue(
			new Error("GET /instances/i1/state → 404"),
		);
		renderCard();
		await waitFor(() =>
			expect(screen.getByText(/No inventory cached yet/i)).toBeTruthy(),
		);
		expect(screen.getByRole("button", { name: /Sync now/i })).toBeTruthy();
	});

	it("renders the cached inventory with per-kind counts", async () => {
		mockApi.instanceState.mockResolvedValue(CACHED);
		renderCard();
		await waitFor(() => expect(screen.getByText("Topologies")).toBeTruthy());
		expect(screen.getByText("solution-design")).toBeTruthy();
		expect(screen.getByText("get-weather")).toBeTruthy();
		expect(screen.getByText(/synced/i)).toBeTruthy();
	});

	it("shows an artifact's content when clicked", async () => {
		mockApi.instanceState.mockResolvedValue(CACHED);
		renderCard();
		const chip = await screen.findByText("solution-design");
		fireEvent.click(chip);
		// content_hash prefix + the JsonBlock content appear.
		await waitFor(() => expect(screen.getByText(/abcdef123456/)).toBeTruthy());
		expect(screen.getByText(/"kind": "Topology"/)).toBeTruthy();
	});

	it("Sync now pulls then revalidates", async () => {
		mockApi.instanceState.mockRejectedValueOnce(new Error("404")); // not synced yet
		mockApi.syncInstance.mockResolvedValue({
			instance_id: "i1",
			synced_at: "x",
			counts: { topologies: 1 },
		});
		mockApi.instanceState.mockResolvedValue(CACHED); // after sync, cache has data
		renderCard();
		fireEvent.click(await screen.findByRole("button", { name: /Sync now/i }));
		await waitFor(() =>
			expect(mockApi.syncInstance).toHaveBeenCalledWith("i1"),
		);
		await waitFor(() =>
			expect(screen.getByText("solution-design")).toBeTruthy(),
		);
	});

	it("adopts a selected artifact into the registry with its kind", async () => {
		mockApi.instanceState.mockResolvedValue(CACHED);
		mockApi.adoptArtifact.mockResolvedValue({
			kind: "skill",
			artifact_id: "get-weather",
			version: "v1",
			content_hash: "deadbeef0000",
			adopted_from: "i1",
			synced_at: "x",
		});
		renderCard();
		// select the skill, then adopt it — the singular kind is sent, not the collection key.
		fireEvent.click(await screen.findByText("get-weather"));
		fireEvent.click(
			await screen.findByRole("button", { name: /Adopt into registry/i }),
		);
		await waitFor(() =>
			expect(mockApi.adoptArtifact).toHaveBeenCalledWith("i1", {
				kind: "skill",
				artifact_id: "get-weather",
			}),
		);
		await waitFor(() =>
			expect(
				screen.getByText(/Adopted skill\/get-weather as v1/i),
			).toBeTruthy(),
		);
	});
});
