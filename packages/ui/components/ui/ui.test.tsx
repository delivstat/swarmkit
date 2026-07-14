import { describe, expect, it } from "vitest";

import { cn } from "@/lib/utils";
import { badgeVariants } from "./badge";
import { buttonVariants } from "./button";

// The primitives are Radix wrappers; here we lock the parts that are ours — the `cn` merge behaviour
// and the CVA variant maps — without a DOM renderer (vitest runs node env here).

describe("cn", () => {
	it("merges and de-dupes conflicting Tailwind utilities (last wins)", () => {
		expect(cn("px-2", "px-4")).toBe("px-4");
		expect(cn("text-sm", false, undefined, "font-medium")).toBe(
			"text-sm font-medium",
		);
		expect(cn("bg-primary", { "bg-secondary": true })).toBe("bg-secondary");
	});
});

describe("buttonVariants", () => {
	it("applies the default variant + size", () => {
		const c = buttonVariants();
		expect(c).toContain("bg-primary");
		expect(c).toContain("h-9");
	});
	it("switches variant and size", () => {
		const c = buttonVariants({ variant: "outline", size: "sm" });
		expect(c).toContain("border-input");
		expect(c).toContain("h-8");
		expect(c).not.toContain("bg-primary text-primary-foreground");
	});
});

describe("badgeVariants", () => {
	it("maps semantic variants to token classes", () => {
		expect(badgeVariants({ variant: "success" })).toContain("text-success");
		expect(badgeVariants({ variant: "destructive" })).toContain(
			"bg-destructive",
		);
		expect(badgeVariants()).toContain("bg-primary");
	});
});
