/**
 * OIDC is opt-in: configured via NEXT_PUBLIC_OIDC_* env. When NEXT_PUBLIC_OIDC_AUTHORITY is unset
 * the UI runs open (no login), matching the panel's open-by-default. Provider-agnostic — works with
 * any OIDC IdP (Auth0, Keycloak, Okta, Entra, …).
 */

export const oidcEnabled = Boolean(process.env.NEXT_PUBLIC_OIDC_AUTHORITY);

export interface OidcSettings {
	authority: string;
	client_id: string;
	redirect_uri: string;
	post_logout_redirect_uri: string;
	scope: string;
	extraQueryParams?: Record<string, string>;
	onSigninCallback: () => void;
}

/** Build oidc-client-ts settings. Must run client-side (reads window.location). */
export function oidcSettings(): OidcSettings {
	const origin = typeof window !== "undefined" ? window.location.origin : "";
	const audience = process.env.NEXT_PUBLIC_OIDC_AUDIENCE;
	return {
		authority: process.env.NEXT_PUBLIC_OIDC_AUTHORITY ?? "",
		client_id: process.env.NEXT_PUBLIC_OIDC_CLIENT_ID ?? "",
		redirect_uri: origin,
		post_logout_redirect_uri: origin,
		scope: process.env.NEXT_PUBLIC_OIDC_SCOPE ?? "openid profile email",
		// Some IdPs (e.g. Auth0) need an explicit audience to issue an API-scoped access token
		// whose `aud` matches the panel's --oidc-audience.
		...(audience ? { extraQueryParams: { audience } } : {}),
		// Strip ?code&state from the URL after the redirect is processed.
		onSigninCallback: () => {
			window.history.replaceState({}, document.title, window.location.pathname);
		},
	};
}
