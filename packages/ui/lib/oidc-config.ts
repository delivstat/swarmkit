/**
 * OIDC settings for the `jwt` auth mode. Everything comes from the serve's /auth-info: issuer,
 * audience, and — since the fix for the unconfigurable-client-id bug — the client_id and scope too.
 *
 * The client_id used to come from NEXT_PUBLIC_OIDC_CLIENT_ID, on the reasoning that the serve
 * validates tokens and does not own the browser's registration. Correct in principle, unshippable
 * in practice: NEXT_PUBLIC_* is inlined by Next.js at BUILD time and this portal ships as a
 * pre-built static export, so the published wheel resolved it to "" on every load and could not be
 * pointed at any identity provider. The env var is kept as a fallback so a self-built UI is
 * unaffected. Provider-agnostic (Auth0, Keycloak, Okta, Entra, …).
 */

export interface OidcSettings {
	authority: string;
	client_id: string;
	redirect_uri: string;
	post_logout_redirect_uri: string;
	scope: string;
	extraQueryParams?: Record<string, string>;
	onSigninCallback: () => void;
}

/** Build oidc-client-ts settings from the discovered issuer/audience. Client-side only (reads
 * window.location). */
export function oidcSettings(discovered: {
	issuer: string;
	audience: string;
	client_id?: string;
	scope?: string;
}): OidcSettings {
	const origin = typeof window !== "undefined" ? window.location.origin : "";
	const pathname =
		typeof window !== "undefined" ? window.location.pathname : "";
	return {
		authority: discovered.issuer,
		// Server-advertised first; the build-time env var is the fallback for a self-built UI.
		client_id:
			discovered.client_id || (process.env.NEXT_PUBLIC_OIDC_CLIENT_ID ?? ""),
		// Return to the current route so the AuthProvider there processes the ?code&state callback.
		redirect_uri: `${origin}${pathname}`,
		post_logout_redirect_uri: origin,
		scope:
			discovered.scope ||
			process.env.NEXT_PUBLIC_OIDC_SCOPE ||
			"openid profile email",
		// Some IdPs (Auth0) need an explicit audience to mint an access token whose `aud` matches
		// the serve's configured audience.
		...(discovered.audience
			? { extraQueryParams: { audience: discovered.audience } }
			: {}),
		onSigninCallback: () => {
			window.history.replaceState({}, document.title, window.location.pathname);
		},
	};
}

/** Whether a sign-in can even be attempted. An empty client_id sends the browser to the IdP with
 * `client_id=`, which every provider rejects with an opaque error page — so the portal must say
 * what is missing instead of bouncing the user out to see it. */
export function missingClientId(settings: OidcSettings): boolean {
	return settings.client_id.trim() === "";
}
