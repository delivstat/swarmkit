/**
 * Module-level holder for the current access token + a 401 handler. The AuthProvider keeps these
 * in sync with the OIDC session; the API client (a plain module, not a hook) reads them so every
 * request carries the bearer and a 401 can trigger re-login.
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
