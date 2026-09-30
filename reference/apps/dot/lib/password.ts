import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

// Password storage: scrypt hash in env (design/details/dot-app.md Q1 default a).
// Format: scrypt$<N>$<r>$<p>$<saltB64>$<hashB64>
// Produce with `node reference/apps/dot/scripts/hash-password.mjs` (see commit 2 helper).

const N = 16384;
const R = 8;
const P = 1;
const KEYLEN = 64;

export function hashPassword(password: string): string {
	const salt = randomBytes(16);
	const hash = scryptSync(password, salt, KEYLEN, { N, r: R, p: P });
	return `scrypt$${N}$${R}$${P}$${salt.toString("base64")}$${hash.toString("base64")}`;
}

export function verifyPassword(password: string, stored: string): boolean {
	const parts = stored.split("$");
	if (parts.length !== 6 || parts[0] !== "scrypt") return false;
	const n = Number(parts[1]);
	const r = Number(parts[2]);
	const p = Number(parts[3]);
	const salt = parts[4];
	const hash = parts[5];
	if (
		!Number.isFinite(n) ||
		!Number.isFinite(r) ||
		!Number.isFinite(p) ||
		!salt ||
		!hash
	) {
		return false;
	}
	const saltBuf = Buffer.from(salt, "base64");
	const expected = Buffer.from(hash, "base64");
	const got = scryptSync(password, saltBuf, expected.length, { N: n, r, p });
	if (got.length !== expected.length) return false;
	return timingSafeEqual(got, expected);
}
