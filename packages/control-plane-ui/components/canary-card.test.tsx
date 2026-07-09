import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { CanaryEnvelope } from "@/lib/types";
import { CanaryCard } from "./canary-card";

vi.mock("@/lib/api", () => ({
	api: {
		instanceCanary: vi.fn(),
		promoteCanary: vi.fn(),
		rollbackCanary: vi.fn(),
		startCanary: vi.fn(),
	},
}));

const mockApi = vi.mocked(api);

const REACHABLE: CanaryEnvelope = {
	reachable: true,
	reason: null,
	canary: {
		enabled: true,
		routes: [
			{
				topology: "solution-design",
				versions: [
					{ version: "1.0.0", weight: 90 },
					{
						version: "1.1.0",
						weight: 10,
						metrics: { total_runs: 20, failed_runs: 1, error_rate: 0.05 },
					},
				],
			},
		],
	},
};

function renderCard() {
	return render(
		<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
			<CanaryCard instanceId="i1" />
		</SWRConfig>,
	);
}

describe("CanaryCard", () => {
	beforeEach(() => vi.clearAllMocks());

	it("shows per-version weight + error rate, tagging the canary", async () => {
		mockApi.instanceCanary.mockResolvedValue(REACHABLE);
		renderCard();
		await waitFor(() => expect(screen.getByText("1.1.0")).toBeTruthy());
		expect(screen.getByText("10%")).toBeTruthy(); // canary weight
		expect(screen.getByText("5.0%")).toBeTruthy(); // error rate
		expect(screen.getByText("canary")).toBeTruthy();
		expect(screen.getByText("stable")).toBeTruthy();
	});

	it("promote calls the api for the canary version", async () => {
		mockApi.instanceCanary.mockResolvedValue(REACHABLE);
		mockApi.promoteCanary.mockResolvedValue({ promoted: true });
		renderCard();
		fireEvent.click(await screen.findByRole("button", { name: /Promote/i }));
		await waitFor(() =>
			expect(mockApi.promoteCanary).toHaveBeenCalledWith(
				"i1",
				"solution-design",
				"1.1.0",
			),
		);
	});

	it("roll back calls the api for the topology", async () => {
		mockApi.instanceCanary.mockResolvedValue(REACHABLE);
		mockApi.rollbackCanary.mockResolvedValue({ rolled_back: true });
		renderCard();
		fireEvent.click(await screen.findByRole("button", { name: /Roll back/i }));
		await waitFor(() =>
			expect(mockApi.rollbackCanary).toHaveBeenCalledWith(
				"i1",
				"solution-design",
			),
		);
	});

	it("shows a poll-mode message and no controls", async () => {
		mockApi.instanceCanary.mockResolvedValue({
			reachable: false,
			reason: "poll-mode",
			canary: { enabled: false, routes: [] },
		});
		renderCard();
		await waitFor(() =>
			expect(screen.getByText(/Poll-mode \(Mode B\)/i)).toBeTruthy(),
		);
		expect(screen.queryByRole("button", { name: /Promote/i })).toBeNull();
	});

	it("starts a canary from the form", async () => {
		mockApi.instanceCanary.mockResolvedValue({
			reachable: true,
			reason: null,
			canary: { enabled: false, routes: [] },
		});
		mockApi.startCanary.mockResolvedValue({ started: true });
		renderCard();
		await waitFor(() =>
			expect(screen.getByText(/No canary routes configured/i)).toBeTruthy(),
		);
		fireEvent.change(screen.getByLabelText("Topology"), {
			target: { value: "advisor" },
		});
		fireEvent.change(screen.getByLabelText("Base version"), {
			target: { value: "2.0.0" },
		});
		fireEvent.change(screen.getByLabelText("Canary version"), {
			target: { value: "2.1.0" },
		});
		fireEvent.change(screen.getByLabelText("Weight"), {
			target: { value: "20" },
		});
		fireEvent.click(screen.getByRole("button", { name: /^Start$/i }));
		await waitFor(() =>
			expect(mockApi.startCanary).toHaveBeenCalledWith("i1", "advisor", {
				base_version: "2.0.0",
				canary_version: "2.1.0",
				weight: 20,
			}),
		);
	});

	it("shows an empty state when no canary routes are configured", async () => {
		mockApi.instanceCanary.mockResolvedValue({
			reachable: true,
			reason: null,
			canary: { enabled: false, routes: [] },
		});
		renderCard();
		await waitFor(() =>
			expect(screen.getByText(/No canary routes configured/i)).toBeTruthy(),
		);
	});
});
