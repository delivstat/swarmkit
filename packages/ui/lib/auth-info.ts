/**
 * Auth discovery. Unlike the fleet UI (which bakes NEXT_PUBLIC_OIDC_* at build time), the workspace
 * UI asks the serve which login gate to render — `GET /auth-info` (unauthenticated). So a local
 * `swarmkit serve` (mode `none`) needs no config and shows no login, while an OIDC-secured serve
 * advertises its issuer/audience.
 */

const BASE = process.env.NEXT_PUBLIC_SWARMKIT_API ?? "";

export type AuthMode = "none" | "api_key" | "jwt";

export interface OidcDiscovery {
	issuer: string;
	audience: string;
	/** The browser's OIDC client registration, served by the workspace.
	 *
	 * This has to come from the server. NEXT_PUBLIC_* values are inlined by Next.js at BUILD time,
	 * and the portal ships as a pre-built static export — so in the published wheel the env var
	 * resolves to "" on every load and no operator can point it at their identity provider. Serving
	 * it makes it workspace configuration, like issuer and audience already are. */
	client_id?: string;
	/** Scopes to request. Server-advertised for the same reason. */
	scope?: string;
}

export interface AuthInfo {
	mode: AuthMode;
	oidc?: OidcDiscovery;
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
