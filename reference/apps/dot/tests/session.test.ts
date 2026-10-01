import { revoke, signSession, verifySession } from "@/lib/session";
import { beforeAll, describe, expect, it } from "vitest";

beforeAll(() => {
	process.env.SESSION_SECRET = "x".repeat(48);
});

describe("session", () => {
	it("round-trips a valid cookie", async () => {
		const { value, payload } = await signSession("owner@example.com");
		const verified = await verifySession(value);
		expect(verified).not.toBeNull();
		expect(verified?.owner).toBe("owner@example.com");
		expect(verified?.jti).toBe(payload.jti);
	});

	it("rejects a tampered payload", async () => {
		const { value } = await signSession("owner@example.com");
		const [body, sig] = value.split(".");
		const tampered = `${body}A.${sig}`;
		expect(await verifySession(tampered)).toBeNull();
	});

	it("rejects a tampered signature", async () => {
		const { value } = await signSession("owner@example.com");
		const [body, sig] = value.split(".");
		if (!sig) throw new Error("signSession produced no signature");
		// Flip a middle char. Tampering the LAST char of a base64url-encoded HMAC-SHA256
		// (43 chars) is non-deterministic — the final char only carries 2 significant bits plus
		// 4 padding bits, so 4 of its 64 possible values decode to the same digest. Middle chars
		// are fully meaningful.
		const i = Math.floor(sig.length / 2);
		const orig = sig[i];
		const replacement = orig === "A" ? "B" : "A";
		const tampered = `${sig.slice(0, i)}${replacement}${sig.slice(i + 1)}`;
		expect(await verifySession(`${body}.${tampered}`)).toBeNull();
	});

	it("rejects an expired cookie", async () => {
		const past = Math.floor(Date.now() / 1000) - 60 * 60 * 24 * 60;
		const { value } = await signSession("owner@example.com", past);
		expect(await verifySession(value)).toBeNull();
	});

	it("rejects an empty or malformed cookie", async () => {
		expect(await verifySession(undefined)).toBeNull();
		expect(await verifySession("")).toBeNull();
		expect(await verifySession("notacookie")).toBeNull();
	});

	it("respects the revocation blocklist", async () => {
		const { value, payload } = await signSession("owner@example.com");
		expect(await verifySession(value)).not.toBeNull();
		revoke(payload.jti, payload.exp);
		expect(await verifySession(value)).toBeNull();
	});
});
