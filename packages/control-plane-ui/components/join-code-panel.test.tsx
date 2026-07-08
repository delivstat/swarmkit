import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { JoinCodePanel } from "./join-code-panel";

vi.mock("@/lib/api", () => ({
	api: { mintJoinCode: vi.fn() },
}));

const mockApi = vi.mocked(api);

describe("JoinCodePanel", () => {
	beforeEach(() => vi.clearAllMocks());

	it("mints a code with the chosen name + tier and shows the connect command", async () => {
		mockApi.mintJoinCode.mockResolvedValue({
			join_code: "code-abc",
			tier: "run",
			expires_in: 900,
		});
		render(<JoinCodePanel />);

		fireEvent.change(screen.getByLabelText(/Name/i), {
			target: { value: "edge-oms" },
		});
		fireEvent.change(screen.getByLabelText(/Granted tier/i), {
			target: { value: "run" },
		});
		fireEvent.click(screen.getByRole("button", { name: /Mint join code/i }));

		await waitFor(() =>
			expect(mockApi.mintJoinCode).toHaveBeenCalledWith({
				name: "edge-oms",
				tier: "run",
			}),
		);
		// the ready-to-run connect command carries the code.
		await waitFor(() =>
			expect(
				screen.getByText(/swarmkit connect .*--join-code code-abc/),
			).toBeTruthy(),
		);
		expect(screen.getByText(/expires in 900s/i)).toBeTruthy();
	});

	it("omits an empty name from the request", async () => {
		mockApi.mintJoinCode.mockResolvedValue({
			join_code: "c",
			tier: "read",
			expires_in: 900,
		});
		render(<JoinCodePanel />);
		fireEvent.click(screen.getByRole("button", { name: /Mint join code/i }));
		await waitFor(() =>
			expect(mockApi.mintJoinCode).toHaveBeenCalledWith({ tier: "read" }),
		);
	});

	it("surfaces a mint error", async () => {
		mockApi.mintJoinCode.mockRejectedValue(new Error("nope"));
		render(<JoinCodePanel />);
		fireEvent.click(screen.getByRole("button", { name: /Mint join code/i }));
		await waitFor(() => expect(screen.getByText("nope")).toBeTruthy());
	});
});
