import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { Instance } from "@/lib/types";
import { EnrollmentPanel } from "./enrollment-panel";

vi.mock("@/lib/api", () => ({
	api: { registerInstance: vi.fn(), refreshInstance: vi.fn() },
}));

const mockApi = vi.mocked(api);

const INSTANCE: Instance = {
	id: "i1",
	name: "edge",
	endpoint: "http://edge:8000",
	connection: "direct",
	tier: "read",
	token_fingerprint: "",
	token_minted_at: null,
	schema_version: "",
	capabilities: {},
	health: "unknown",
	last_seen: null,
	created_at: "2026-07-08T00:00:00Z",
};

function renderPanel() {
	const onChanged = vi.fn();
	render(<EnrollmentPanel instance={INSTANCE} onChanged={onChanged} />);
	return { onChanged };
}

describe("EnrollmentPanel", () => {
	beforeEach(() => vi.clearAllMocks());

	it("registers with the entered token + requested scope and shows the result", async () => {
		mockApi.registerInstance.mockResolvedValue({
			membership_id: "mem-1",
			scope: "manage",
			fingerprint: "abc123",
			synced_at: "2026-07-08T01:00:00Z",
			counts: { topologies: 2, skills: 1 },
		});
		const { onChanged } = renderPanel();

		fireEvent.change(screen.getByLabelText(/Enrollment token/i), {
			target: { value: "join-code-xyz" },
		});
		fireEvent.change(screen.getByLabelText(/Requested scope/i), {
			target: { value: "manage" },
		});
		fireEvent.click(screen.getByRole("button", { name: /^Register$/i }));

		await waitFor(() =>
			expect(mockApi.registerInstance).toHaveBeenCalledWith("i1", {
				enroll_token: "join-code-xyz",
				requested_scope: "manage",
			}),
		);
		await waitFor(() => expect(screen.getByText(/Enrolled\./i)).toBeTruthy());
		expect(screen.getByText(/mem-1/)).toBeTruthy();
		expect(screen.getByText(/2 topologies/)).toBeTruthy();
		expect(onChanged).toHaveBeenCalled();
	});

	it("blocks an empty-token register without calling the API", async () => {
		renderPanel();
		fireEvent.click(screen.getByRole("button", { name: /^Register$/i }));
		await waitFor(() =>
			expect(
				screen.getByText(/Enter the one-time enrollment token/i),
			).toBeTruthy(),
		);
		expect(mockApi.registerInstance).not.toHaveBeenCalled();
	});

	it("omits requested_scope when left at token default", async () => {
		mockApi.registerInstance.mockResolvedValue({
			membership_id: "mem-2",
			scope: "monitor",
			fingerprint: "def456",
			synced_at: "x",
			counts: {},
		});
		renderPanel();
		fireEvent.change(screen.getByLabelText(/Enrollment token/i), {
			target: { value: "code" },
		});
		fireEvent.click(screen.getByRole("button", { name: /^Register$/i }));
		await waitFor(() =>
			expect(mockApi.registerInstance).toHaveBeenCalledWith("i1", {
				enroll_token: "code",
			}),
		);
	});

	it("rotates the key and shows the rotated fingerprint", async () => {
		mockApi.refreshInstance.mockResolvedValue({
			membership_id: "mem-1",
			scope: "manage",
			fingerprint: "rotated99",
		});
		renderPanel();
		fireEvent.click(screen.getByRole("button", { name: /Rotate key/i }));
		await waitFor(() =>
			expect(mockApi.refreshInstance).toHaveBeenCalledWith("i1"),
		);
		await waitFor(() =>
			expect(screen.getByText(/Key rotated\./i)).toBeTruthy(),
		);
		expect(screen.getByText(/rotated99/)).toBeTruthy();
	});

	it("surfaces a rotate-before-register error", async () => {
		mockApi.refreshInstance.mockRejectedValue(
			new Error("no membership credential for this instance — register first"),
		);
		renderPanel();
		fireEvent.click(screen.getByRole("button", { name: /Rotate key/i }));
		await waitFor(() =>
			expect(screen.getByText(/register first/i)).toBeTruthy(),
		);
	});
});
