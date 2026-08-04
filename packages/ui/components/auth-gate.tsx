"use client";

import { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "react-oidc-context";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	type AuthInfo,
	fetchAuthInfo,
	loadStoredApiKey,
	storeApiKey,
} from "@/lib/auth-info";
import { missingClientId, oidcSettings } from "@/lib/oidc-config";
import { setAccessToken, setUnauthorizedHandler } from "@/lib/token-store";

function Centered({ children }: { children: React.ReactNode }) {
	return (
		<div className="flex h-screen w-full flex-col items-center justify-center gap-4 p-6 text-center text-foreground">
			{children}
		</div>
	);
}

function Title({ subtitle }: { subtitle: string }) {
	return (
		<div className="space-y-1">
			<h1 className="text-xl font-semibold">SwarmKit Workspace</h1>
			<p className="text-sm text-muted-foreground">{subtitle}</p>
		</div>
	);
}

/** jwt mode: OIDC PKCE via react-oidc-context (same flow the fleet UI ships), with the issuer +
 * audience discovered from /auth-info. */
function OidcGate({ children }: { children: React.ReactNode }) {
	const auth = useAuth();
	const token = auth.user?.access_token ?? null;

	// Sync the token into the store DURING render — child data fetches fire before parent effects,
	// so an effect here would let the first API call race ahead token-less (401 → re-login loop).
	// Setting an external store in render is idempotent + safe.
	setAccessToken(token);

	useEffect(() => {
		setUnauthorizedHandler(() => void auth.signinRedirect());
		return () => setUnauthorizedHandler(null);
	}, [auth]);

	if (auth.isLoading) return <Centered>Signing in…</Centered>;
	if (!auth.isAuthenticated) {
		return (
			<Centered>
				<Title subtitle="This workspace requires sign-in." />
				{auth.error ? (
					<p className="max-w-md text-sm text-destructive">
						{auth.error.message}
					</p>
				) : null}
				<Button type="button" onClick={() => void auth.signinRedirect()}>
					Sign in with your identity provider
				</Button>
				<TokenFallback />
			</Centered>
		);
	}
	return <>{children}</>;
}

/** A collapsed "use a token instead" affordance for `jwt` mode.
 *
 * The transport is identical — every API call already sends the stored value as `Authorization:
 * Bearer` — so a token minted by a CLI, issued by CI, or obtained while setting up the IdP works
 * today and had no way in. It also means a broken redirect flow no longer locks the portal
 * completely, which is exactly what the unconfigurable-client-id bug did. */
function TokenFallback() {
	const [open, setOpen] = useState(false);
	const [draft, setDraft] = useState("");

	if (!open) {
		return (
			<button
				type="button"
				className="text-sm text-muted-foreground underline underline-offset-4"
				onClick={() => setOpen(true)}
			>
				Use a token instead
			</button>
		);
	}
	return (
		<form
			className="flex w-full max-w-sm flex-col gap-2"
			onSubmit={(e) => {
				e.preventDefault();
				const value = draft.trim();
				if (!value) return;
				storeApiKey(value);
				setAccessToken(value);
				window.location.reload();
			}}
		>
			<Input
				type="password"
				value={draft}
				onChange={(e) => setDraft(e.target.value)}
				placeholder="Bearer token"
			/>
			<Button type="submit">Continue</Button>
		</form>
	);
}

/** api_key mode: a lightweight key gate. The key is stored in localStorage and attached as the
 * bearer; a 401 clears it and re-prompts. */
function ApiKeyGate({ children }: { children: React.ReactNode }) {
	const [key, setKey] = useState<string | null>(() => loadStoredApiKey());
	const [draft, setDraft] = useState("");

	setAccessToken(key); // sync during render (see OidcGate note)

	useEffect(() => {
		setUnauthorizedHandler(() => {
			storeApiKey(null);
			setAccessToken(null);
			setKey(null);
		});
		return () => setUnauthorizedHandler(null);
	}, []);

	if (key) return <>{children}</>;

	return (
		<Centered>
			<Title subtitle="This workspace requires an API key." />
			<form
				className="flex w-full max-w-sm flex-col gap-2"
				onSubmit={(e) => {
					e.preventDefault();
					const value = draft.trim();
					if (!value) return;
					storeApiKey(value);
					setKey(value);
				}}
			>
				<Input
					type="password"
					value={draft}
					onChange={(e) => setDraft(e.target.value)}
					placeholder="Bearer API key"
				/>
				<Button type="submit">Continue</Button>
			</form>
		</Centered>
	);
}

/** Wraps the app. Asks the serve which login gate to render (/auth-info): `none` → open (local dev,
 * no login); `api_key` → key gate; `jwt` → OIDC login. */
export function AuthGate({ children }: { children: React.ReactNode }) {
	const [info, setInfo] = useState<AuthInfo | null>(null);
	const [mounted, setMounted] = useState(false);

	useEffect(() => {
		setMounted(true);
		void fetchAuthInfo().then(setInfo);
	}, []);

	// Avoid touching window/localStorage during SSR/prerender, and wait for discovery.
	if (!mounted || info === null) return <Centered>Connecting…</Centered>;
	if (info.mode === "none") return <>{children}</>;
	if (info.mode === "api_key") return <ApiKeyGate>{children}</ApiKeyGate>;
	if (!info.oidc) {
		return (
			<Centered>
				Server requires OIDC sign-in but advertised no issuer. Check the
				serve&apos;s
				<code> server.auth</code> config.
			</Centered>
		);
	}
	const settings = oidcSettings(info.oidc);
	if (missingClientId(settings)) {
		// Do not attempt the redirect. `client_id=` sends the user to the IdP for an opaque
		// rejection page that says nothing about which side is misconfigured — the portal knows
		// exactly what is missing and should say so here. A token still gets you in meanwhile.
		return (
			<Centered>
				<Title subtitle="Sign-in is not configured." />
				<p className="max-w-md text-sm text-muted-foreground">
					The server did not advertise an OIDC <code>client_id</code>. Set{" "}
					<code>server.auth.config.client_id</code> in workspace.yaml to the
					client id registered with your identity provider, then restart serve.
				</p>
				<TokenFallback />
			</Centered>
		);
	}
	return (
		<AuthProvider {...settings}>
			<OidcGate>{children}</OidcGate>
		</AuthProvider>
	);
}
