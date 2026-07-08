import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { Membership } from "@/lib/types";
import { MembershipCard } from "./membership-card";

vi.mock("@/lib/api", () => ({
	api: { membership: vi.fn(), leaveFleet: vi.fn() },
}));

const mockApi = vi.mocked(api);

const MEMBERSHIP: Membership = {
	membership_id: "mem-1",
	fleet_id: "swarmkit-fleet",
	scope: "manage",
	fingerprint: "abc123",
	created_at: "2026-07-08T00:00:00Z",
};

function renderCard() {
	return render(
		<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
			<MembershipCard instanceId="i1" />
		</SWRConfig>,
	);
}

describe("MembershipCard", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.stubGlobal("confirm", () => true);
	});

	it("renders this fleet's membership metadata", async () => {
		mockApi.membership.mockResolvedValue(MEMBERSHIP);
		renderCard();
		await waitFor(() =>
			expect(screen.getByText("swarmkit-fleet")).toBeTruthy(),
		);
		expect(screen.getByText("manage")).toBeTruthy();
		expect(screen.getByText(/mem-1/)).toBeTruthy();
	});

	it("shows an empty state when this fleet holds no membership (404)", async () => {
		mockApi.membership.mockRejectedValue(new Error("404"));
		renderCard();
		await waitFor(() =>
			expect(screen.getByText(/holds no membership/i)).toBeTruthy(),
		);
		// no Leave button without a membership.
		expect(screen.queryByRole("button", { name: /Leave fleet/i })).toBeNull();
	});

	it("leaves the fleet and revalidates", async () => {
		mockApi.membership.mockResolvedValue(MEMBERSHIP);
		mockApi.leaveFleet.mockResolvedValue({
			left: "swarmkit-fleet",
			membership_id: "mem-1",
		});
		renderCard();
		fireEvent.click(
			await screen.findByRole("button", { name: /Leave fleet/i }),
		);
		await waitFor(() => expect(mockApi.leaveFleet).toHaveBeenCalledWith("i1"));
	});
});
