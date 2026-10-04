// Signed session cookie: base64url(JSON payload).base64url(hmac-sha256(payload, SESSION_SECRET)).
// The design note (design/details/dot-app.md §Auth model / §Session management) fixes the shape;
// the discipline here matches the fleet panel's SWARMKIT_CONTROL_PLANE_SECRET_KEY handling.
//
// Uses Web Crypto (SubtleCrypto) so the same module runs in both the Edge runtime (middleware)
// and Node (route handlers). No node:crypto import.

export const COOKIE_NAME = "dot_session";
const MAX_AGE_S = 30 * 24 * 60 * 60;

export interface SessionPayload {
	owner: string;
	iat: number;
	exp: number;
	jti: string;
}

function b64urlEncode(bytes: Uint8Array): string {
	let s = "";
	for (const b of bytes) s += String.fromCharCode(b);
	return btoa(s).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function b64urlDecode(s: string): Uint8Array {
	const pad = s.length % 4 === 0 ? "" : "=".repeat(4 - (s.length % 4));
	const bin = atob(s.replaceAll("-", "+").replaceAll("_", "/") + pad);
	const out = new Uint8Array(bin.length);
	for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
	return out;
}

let cachedKey: CryptoKey | null = null;
let cachedSecret: string | null = null;

async function key(): Promise<CryptoKey> {
	const s = process.env.SESSION_SECRET;
	if (!s || s.length < 32) {
		throw new Error(
			"SESSION_SECRET must be set to a 32+ character random string (see design/details/dot-app.md §Session management).",
		);
	}
	if (cachedKey && cachedSecret === s) return cachedKey;
	const raw = new TextEncoder().encode(s);
	cachedKey = await crypto.subtle.importKey("raw", raw, { name: "HMAC", hash: "SHA-256" }, false, [
		"sign",
		"verify",
	]);
	cachedSecret = s;
	return cachedKey;
}

function randomJti(): string {
	const bytes = new Uint8Array(12);
	crypto.getRandomValues(bytes);
	return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function signSession(
	owner: string,
	now = Math.floor(Date.now() / 1000),
): Promise<{ value: string; maxAge: number; payload: SessionPayload }> {
	const payload: SessionPayload = {
		owner,
		iat: now,
		exp: now + MAX_AGE_S,
		jti: randomJti(),
	};
	const body = b64urlEncode(new TextEncoder().encode(JSON.stringify(payload)));
	const sig = new Uint8Array(
		await crypto.subtle.sign("HMAC", await key(), new TextEncoder().encode(body) as BufferSource),
	);
	return { value: `${body}.${b64urlEncode(sig)}`, maxAge: MAX_AGE_S, payload };
}

export async function verifySession(
	cookie: string | undefined,
	now = Math.floor(Date.now() / 1000),
): Promise<SessionPayload | null> {
	if (!cookie) return null;
	const dot = cookie.indexOf(".");
	if (dot < 1 || dot === cookie.length - 1) return null;
	const body = cookie.slice(0, dot);
	const sig = cookie.slice(dot + 1);
	let sigBytes: Uint8Array;
	try {
		sigBytes = b64urlDecode(sig);
	} catch {
		return null;
	}
	const ok = await crypto.subtle.verify(
		"HMAC",
		await key(),
		sigBytes as BufferSource,
		new TextEncoder().encode(body) as BufferSource,
	);
	if (!ok) return null;
	let payload: SessionPayload;
	try {
		payload = JSON.parse(new TextDecoder().decode(b64urlDecode(body))) as SessionPayload;
	} catch {
		return null;
	}
	if (!payload.owner || !payload.exp || payload.exp < now) return null;
	if (revoked.has(payload.jti)) return null;
	return payload;
}

// Owner-only tool: the blocklist holds at most one jti at a time. Cleaned lazily.
const revoked = new Map<string, number>();

export function revoke(jti: string, exp: number): void {
	const now = Math.floor(Date.now() / 1000);
	for (const [k, e] of revoked) if (e < now) revoked.delete(k);
	revoked.set(jti, exp);
}

export function cookieAttributes(secure: boolean, maxAge: number): string {
	const parts = ["Path=/", "HttpOnly", "SameSite=Lax", `Max-Age=${maxAge}`];
	if (secure) parts.push("Secure");
	return parts.join("; ");
}

export const NEAR_EXPIRY_S = 7 * 24 * 60 * 60;
