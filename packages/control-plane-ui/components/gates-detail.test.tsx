import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import type { GatesEnvelope, ReviewGate } from "@/lib/types";
import { GatesDetail } from "./gates-detail";

vi.mock("@/lib/api", async (importOriginal) => {
	const mod = await importOriginal<typeof import("@/lib/api")>();
	return {
		ApiError: mod.ApiError,
		api: { instanceGates: vi.fn(), resolveGate: vi.fn() },
	};
});

const mockApi = vi.mocked(api);

const TASK: ReviewGate = {
	id: "mpa-run-42:analyst-0-engineering-lead",
	kind: "role_task",
	agent_id: "analyst",
	reason: "role 'engineering-lead' must approve 'design:approve'",
	capability: "",
	question: "",
	options: [],
	free_text_allowed: true,
	gate_id: "run-42:analyst",
	run_id: "run-42",
	role: "engineering-lead",
	scope: "design:approve",
};

const ROLE_TASK: GatesEnvelope = {
	reachable: true,
	reason: null,
	gates: [TASK],
};

function renderCard() {
	return render(
		<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
			<GatesDetail instanceId="i1" instanceName="edge-1" />
		</SWRConfig>,
	);
}

describe("GatesDetail — multi-party role-task", () => {
	beforeEach(() => vi.clearAllMocks());

	it("resolves a role-task with the resolve verb and an outcome, not the generic approve", async () => {
		mockApi.instanceGates.mockResolvedValue(ROLE_TASK);
		mockApi.resolveGate.mockResolvedValue(TASK);
		renderCard();
		await screen.findByText("engineering-lead");
		expect(screen.getByText("design:approve")).toBeTruthy();
		fireEvent.click(screen.getByRole("button", { name: "Request changes" }));
		await waitFor(() =>
			expect(mockApi.resolveGate).toHaveBeenCalledWith(
				"i1",
				"mpa-run-42:analyst-0-engineering-lead",
				"resolve",
				"",
				"changes-requested",
			),
		);
	});

	it("shows the instance's refusal on the card", async () => {
		mockApi.instanceGates.mockResolvedValue(ROLE_TASK);
		mockApi.resolveGate.mockRejectedValue(
			new ApiError(
				"POST → 403",
				403,
				"panel is not a member of role engineering-lead",
			),
		);
		renderCard();
		await screen.findByText("engineering-lead");
		fireEvent.click(screen.getByRole("button", { name: "Approve" }));
		const alert = await screen.findByRole("alert");
		expect(alert.textContent).toContain(
			"not a member of role engineering-lead",
		);
	});

	it("says who a resolution counts as", async () => {
		mockApi.instanceGates.mockResolvedValue({
			...ROLE_TASK,
			resolves_as: { kind: "subject", subject: "alice" },
		});
		renderCard();
		const who = await screen.findByTestId("resolves-as");
		expect(who.textContent).toContain("Resolving as alice");
	});

	it("says when it can only act as the enrolment key, and why", async () => {
		mockApi.instanceGates.mockResolvedValue({
			...ROLE_TASK,
			resolves_as: {
				kind: "instance-key",
				reason: "no signed-in operator (OIDC) on this panel",
			},
		});
		renderCard();
		const who = await screen.findByTestId("resolves-as");
		expect(who.textContent).toContain("enrolment key");
		expect(who.textContent).toContain("no signed-in operator");
	});
});
