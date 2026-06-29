"use client";

import { LogOut, Server, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "react-oidc-context";

import { Button } from "@/components/ui/button";
import { oidcEnabled, oidcSettings } from "@/lib/oidc-config";
import { setAccessToken, setUnauthorizedHandler } from "@/lib/token-store";

function Centered({ children }: { children: React.ReactNode }) {
	return (
		<div className="flex h-screen w-full flex-col items-center justify-center gap-4 p-6 text-center">
			{children}
		</div>
	);
}

function Login({ onSignin, error }: { onSignin: () => void; error?: string }) {
	return (
		<Centered>
			<span className="flex size-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
				<Server className="size-6" />
			</span>
			<div className="space-y-1">
				<h1 className="text-xl font-semibold">SwarmKit Fleet</h1>
				<p className="text-sm text-muted-foreground">
					Sign in to manage the fleet.
				</p>
			</div>
			{error ? (
				<p className="max-w-md text-sm text-destructive">{error}</p>
			) : null}
			<Button onClick={onSignin}>
				<ShieldCheck />
				Sign in with your identity provider
			</Button>
		</Centered>
	);
}

function Gate({ children }: { children: React.ReactNode }) {
	const auth = useAuth();
	const token = auth.user?.access_token ?? null;

	useEffect(() => {
		setAccessToken(token);
	}, [token]);

	useEffect(() => {
		// A 401 from the panel re-initiates login (e.g. after token expiry).
		setUnauthorizedHandler(() => {
			void auth.signinRedirect();
		});
		return () => setUnauthorizedHandler(null);
	}, [auth]);

	if (auth.isLoading) return <Centered>Signing in…</Centered>;
	if (!auth.isAuthenticated) {
		return (
			<Login
				onSignin={() => void auth.signinRedirect()}
				error={auth.error?.message}
			/>
		);
	}
	return <>{children}</>;
}

/** Wraps the app: open when OIDC is unconfigured, otherwise gates behind login. */
export function AuthGate({ children }: { children: React.ReactNode }) {
	const [mounted, setMounted] = useState(false);
	useEffect(() => setMounted(true), []);

	if (!oidcEnabled) return <>{children}</>;
	// Avoid constructing the OIDC client (touches window/localStorage) during SSR/prerender.
	if (!mounted) return null;
	return (
		<AuthProvider {...oidcSettings()}>
			<Gate>{children}</Gate>
		</AuthProvider>
	);
}

function SignOutInner() {
	const auth = useAuth();
	const name =
		auth.user?.profile.email ?? auth.user?.profile.name ?? "signed in";
	return (
		<div className="space-y-2 border-t p-3 text-xs text-muted-foreground">
			<div className="truncate" title={String(name)}>
				{String(name)}
			</div>
			<Button
				variant="outline"
				size="sm"
				className="w-full"
				onClick={() => void auth.removeUser()}
			>
				<LogOut />
				Sign out
			</Button>
		</div>
	);
}

/** Sidebar footer control — renders nothing unless OIDC is enabled (and thus inside AuthProvider). */
export function SignOut() {
	if (!oidcEnabled) return null;
	return <SignOutInner />;
}
