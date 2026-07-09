import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { RunsEnvelope } from "@/lib/types";
import { RunsDetail } from "./runs-detail";

vi.mock("@/lib/api", () => ({
	api: { instanceRuns: vi.fn() },
}));

const mockApi = vi.mocked(api);

const REACHABLE: RunsEnvelope = {
	reachable: true,
	reason: null,
	runs: [
		{
			job_id: "j1",
			topology: "solution-design",
			status: "completed",
			usage_input_tokens: 1800,
			usage_output_tokens: 320,
			usage_cost_usd: 0.0421,
		},
		{
			job_id: "j2",
			topology: "code-review",
			status: "failed",
			usage_input_tokens: 500,
			usage_output_tokens: 40,
			usage_cost_usd: 0.008,
		},
	],
};

function renderDetail() {
	return render(
		<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
			<RunsDetail instanceId="i1" instanceName="alpha" />
		</SWRConfig>,
	);
}

describe("RunsDetail", () => {
	beforeEach(() => vi.clearAllMocks());

	it("renders each run with its per-run cost", async () => {
		mockApi.instanceRuns.mockResolvedValue(REACHABLE);
		renderDetail();
		await waitFor(() =>
			expect(screen.getByText("solution-design")).toBeTruthy(),
		);
		expect(screen.getByText("$0.0421")).toBeTruthy(); // per-run cost, what the user wanted
		expect(screen.getByText("code-review")).toBeTruthy();
	});

	it("filters runs by the search box", async () => {
		mockApi.instanceRuns.mockResolvedValue(REACHABLE);
		renderDetail();
		await waitFor(() =>
			expect(screen.getByText("solution-design")).toBeTruthy(),
		);
		fireEvent.change(screen.getByLabelText("Search runs"), {
			target: { value: "code" },
		});
		expect(screen.queryByText("solution-design")).toBeNull();
		expect(screen.getByText("code-review")).toBeTruthy();
	});

	it("shows an honest message for a poll-mode (Mode B) instance", async () => {
		mockApi.instanceRuns.mockResolvedValue({
			reachable: false,
			reason: "poll-mode",
			runs: [],
		});
		renderDetail();
		await waitFor(() =>
			expect(screen.getByText(/poll-mode \(Mode B\)/i)).toBeTruthy(),
		);
	});

	it("shows unavailable when the instance is unreachable", async () => {
		mockApi.instanceRuns.mockResolvedValue({
			reachable: false,
			reason: "unreachable",
			runs: [],
		});
		renderDetail();
		await waitFor(() =>
			expect(screen.getByText(/Instance unavailable/i)).toBeTruthy(),
		);
	});

	it("shows an empty state when the instance has no runs yet", async () => {
		mockApi.instanceRuns.mockResolvedValue({
			reachable: true,
			reason: null,
			runs: [],
		});
		renderDetail();
		await waitFor(() =>
			expect(screen.getByText(/No runs recorded/i)).toBeTruthy(),
		);
	});
});
