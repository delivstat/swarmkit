/**
 * OIDC settings for the `jwt` auth mode. Adapted from the fleet UI: the issuer + audience come from
 * the serve's /auth-info (discovered), while the client_id is this UI's own registration
 * (NEXT_PUBLIC_OIDC_CLIENT_ID) — the serve validates tokens, it doesn't own the browser client id.
 * Provider-agnostic (Auth0, Keycloak, Okta, Entra, …).
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
}): OidcSettings {
	const origin = typeof window !== "undefined" ? window.location.origin : "";
	const pathname =
		typeof window !== "undefined" ? window.location.pathname : "";
	return {
		authority: discovered.issuer,
		client_id: process.env.NEXT_PUBLIC_OIDC_CLIENT_ID ?? "",
		// Return to the current route so the AuthProvider there processes the ?code&state callback.
		redirect_uri: `${origin}${pathname}`,
		post_logout_redirect_uri: origin,
		scope: process.env.NEXT_PUBLIC_OIDC_SCOPE ?? "openid profile email",
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
