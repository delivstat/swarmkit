/**
 * Module-level holder for the current bearer token + a 401 handler. The auth gate keeps these in
 * sync (OIDC session or a stored API key); the API client (a plain module, not a hook) reads them
 * so every request carries the bearer and a 401 can trigger re-auth. Copied from the fleet UI's
 * token-store and kept deliberately small — the one auth sliver worth keeping in sync by hand.
 */

let accessToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAccessToken(token: string | null): void {
	accessToken = token;
}

export function getAccessToken(): string | null {
	return accessToken;
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
	onUnauthorized = handler;
}

export function handleUnauthorized(): void {
	onUnauthorized?.();
}
