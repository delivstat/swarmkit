import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InstanceSelector } from "./instance-selector";

const setSelectedId = vi.fn();
const state = {
	instances: [
		{ id: "i1", name: "alpha" },
		{ id: "i2", name: "bravo" },
	],
	selectedId: "",
	selected: null,
	setSelectedId,
	loading: false,
};

vi.mock("@/lib/instance-context", () => ({
	useInstances: () => state,
}));

describe("InstanceSelector", () => {
	it("offers an 'All instances' option plus every instance, defaulting to All", () => {
		render(<InstanceSelector />);
		const select = screen.getByLabelText("Instance") as HTMLSelectElement;
		const options = Array.from(select.options).map((o) => o.text);
		expect(options).toEqual(["All instances", "alpha", "bravo"]);
		expect(select.value).toBe(""); // "" = All instances is the default
	});

	it("propagates a chosen instance to the context", () => {
		render(<InstanceSelector />);
		fireEvent.change(screen.getByLabelText("Instance"), {
			target: { value: "i2" },
		});
		expect(setSelectedId).toHaveBeenCalledWith("i2");
	});
});
