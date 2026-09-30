import { hashPassword, verifyPassword } from "@/lib/password";
import { describe, expect, it } from "vitest";

describe("password", () => {
	it("verifies a matching password", () => {
		const stored = hashPassword("hunter2");
		expect(verifyPassword("hunter2", stored)).toBe(true);
	});

	it("rejects a mismatching password", () => {
		const stored = hashPassword("hunter2");
		expect(verifyPassword("hunter3", stored)).toBe(false);
	});

	it("rejects a malformed stored value", () => {
		expect(verifyPassword("hunter2", "not-a-scrypt-hash")).toBe(false);
		expect(verifyPassword("hunter2", "scrypt$16384$8$1$onlyfiveparts")).toBe(
			false,
		);
	});

	it("produces a different hash for the same password each call", () => {
		expect(hashPassword("hunter2")).not.toBe(hashPassword("hunter2"));
	});
});
