import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { JsonBlock } from "./json-block";
import { ResourceView, SelectableRow } from "./resource-view";
import { StatusBadge } from "./status-badge";

describe("ResourceView", () => {
	it("shows a busy skeleton while loading", () => {
		render(
			<ResourceView loading>
				<span>data</span>
			</ResourceView>,
		);
		expect(screen.queryByText("data")).toBeNull();
		expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();
	});

	it("shows the error in an alert region", () => {
		render(
			<ResourceView loading={false} error="nope">
				<span>data</span>
			</ResourceView>,
		);
		const alert = screen.getByRole("alert");
		expect(alert.textContent).toBe("nope");
		expect(screen.queryByText("data")).toBeNull();
	});

	it("shows the empty label when empty", () => {
		render(
			<ResourceView loading={false} isEmpty emptyLabel="no rows">
				<span>data</span>
			</ResourceView>,
		);
		expect(screen.getByText("no rows")).not.toBeNull();
	});

	it("renders children once data is present", () => {
		render(
			<ResourceView loading={false}>
				<span>data</span>
			</ResourceView>,
		);
		expect(screen.getByText("data")).not.toBeNull();
	});
});

describe("SelectableRow", () => {
	it("activates on click and on Enter/Space (keyboard accessible)", () => {
		const onSelect = vi.fn();
		render(
			<SelectableRow as="div" onSelect={onSelect}>
				row
			</SelectableRow>,
		);
		const row = screen.getByRole("button");
		expect(row.getAttribute("tabindex")).toBe("0");
		fireEvent.click(row);
		fireEvent.keyDown(row, { key: "Enter" });
		fireEvent.keyDown(row, { key: " " });
		expect(onSelect).toHaveBeenCalledTimes(3);
	});
});

describe("StatusBadge", () => {
	it("maps known statuses to variants and echoes the label", () => {
		render(<StatusBadge status="approved" />);
		expect(screen.getByText("approved").className).toContain("success");
		render(<StatusBadge status="FAILED" />);
		expect(screen.getByText("FAILED").className).toContain("destructive");
	});

	it("falls back to muted for unknown statuses", () => {
		render(<StatusBadge status="weird-thing" />);
		expect(screen.getByText("weird-thing").className).toContain("muted");
	});
});

describe("JsonBlock", () => {
	it("pretty-prints objects and shows strings verbatim", () => {
		render(<JsonBlock value={{ a: 1 }} />);
		expect(screen.getByText(/"a": 1/)).not.toBeNull();
		render(<JsonBlock value="plain text" />);
		expect(screen.getByText("plain text")).not.toBeNull();
	});
});
