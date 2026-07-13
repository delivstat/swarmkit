/**
 * Auth discovery. Unlike the fleet UI (which bakes NEXT_PUBLIC_OIDC_* at build time), the workspace
 * UI asks the serve which login gate to render — `GET /auth-info` (unauthenticated). So a local
 * `swarmkit serve` (mode `none`) needs no config and shows no login, while an OIDC-secured serve
 * advertises its issuer/audience.
 */

const BASE = process.env.NEXT_PUBLIC_SWARMKIT_API ?? "";

export type AuthMode = "none" | "api_key" | "jwt";

export interface AuthInfo {
	mode: AuthMode;
	oidc?: { issuer: string; audience: string };
}

/** Fetch the serve's auth mode. Falls back to `none` if the endpoint is unreachable/old (so the UI
 * degrades to open rather than locking the user out of a dev serve). */
export async function fetchAuthInfo(): Promise<AuthInfo> {
	try {
		const res = await fetch(`${BASE}/auth-info`);
		if (!res.ok) return { mode: "none" };
		const data = (await res.json()) as Partial<AuthInfo>;
		if (data.mode === "api_key" || data.mode === "jwt") {
			return { mode: data.mode, oidc: data.oidc };
		}
		return { mode: "none" };
	} catch {
		return { mode: "none" };
	}
}

const API_KEY_STORAGE = "swarmkit.workspace.apiKey";

export function loadStoredApiKey(): string | null {
	if (typeof window === "undefined") return null;
	return window.localStorage.getItem(API_KEY_STORAGE);
}

export function storeApiKey(key: string | null): void {
	if (typeof window === "undefined") return;
	if (key) window.localStorage.setItem(API_KEY_STORAGE, key);
	else window.localStorage.removeItem(API_KEY_STORAGE);
}
