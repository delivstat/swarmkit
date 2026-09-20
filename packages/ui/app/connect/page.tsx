"use client";

/**
 * Connect your accounts.
 *
 * The page for someone who uses this agent but does not run it. It answers one question — which
 * accounts will this agent use *as me*, and which have I connected — and it can answer nothing
 * else.
 *
 * It is a separate route from `/connections` rather than that page filtered by role, and it reads
 * a separate endpoint. A filtered operator page has already received the roster it declines to
 * draw: the data is disclosed the moment it is serialised, and `view-source` is the whole exploit.
 * `GET /api/oauth/my-credentials` takes no owner parameter, so this page cannot ask about anyone
 * else even if a later change tried to make it.
 *
 * Design: `design/details/per-caller-credential-delegation.md`.
 */

import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { api } from "@/lib/api";
import type { MyCredential } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useCallback, useEffect, useState } from "react";

/** A popup, not a redirect: the provider returns to a callback that closes itself. */
const POPUP = "width=520,height=680,menubar=no,toolbar=no";

function expiryNote(credential: MyCredential): string | null {
	if (!credential.connected) return null;
	if (credential.expired) return "Expired — reconnect to keep it working.";
	const seconds = credential.seconds_remaining;
	if (seconds != null && seconds < 60 * 60 * 24) {
		return "Expires soon; it will refresh itself unless the grant was revoked.";
	}
	return null;
}

export default function ConnectPage() {
	const [rows, setRows] = useState<MyCredential[] | null>(null);
	const [owner, setOwner] = useState<string>("");
	const [busy, setBusy] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);

	const load = useCallback(async () => {
		try {
			const result = await api.myCredentials();
			setRows(result.credentials);
			setOwner(result.owner);
			setError(null);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		}
	}, []);

	useEffect(() => {
		void load();
	}, [load]);

	const connect = useCallback(async (credential: MyCredential) => {
		if (!credential.endpoint) return;
		setBusy(credential.credential_id);
		setError(null);
		try {
			const { authorization_url } = await api.oauthLogin(
				credential.credential_id,
				credential.endpoint,
			);
			window.open(authorization_url, "swarmkit-oauth", POPUP);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(null);
		}
	}, []);

	const disconnect = useCallback(
		async (credential: MyCredential) => {
			setBusy(credential.credential_id);
			setError(null);
			try {
				await api.oauthDisconnect(credential.credential_id);
				await load();
			} catch (err) {
				setError(err instanceof Error ? err.message : String(err));
			} finally {
				setBusy(null);
			}
		},
		[load],
	);

	// Only per-user connections are this person's to act on. A global one is the operator's, and
	// showing it with a Connect button would invite someone to "fix" a connection that is not
	// theirs and is not broken.
	const mine = (rows ?? []).filter((c) => c.identity === "per-user");
	const shared = (rows ?? []).filter((c) => c.identity === "global");

	return (
		<div className="mx-auto w-full max-w-3xl space-y-6 p-6">
			<header className="space-y-1">
				<h1 className="text-2xl font-semibold">Connect your accounts</h1>
				<p className="text-sm text-muted-foreground">
					These are the accounts this agent uses <em>as you</em>. Connecting one
					lets it reach your data and nobody else&apos;s
					{owner ? ` — you are signed in as ${owner}.` : "."}
				</p>
			</header>

			{error ? (
				<p className="rounded-md border border-destructive/40 p-3 text-sm text-destructive">
					{error}
				</p>
			) : null}

			{rows === null ? (
				<p className="text-sm text-muted-foreground">Loading…</p>
			) : mine.length === 0 ? (
				<Card>
					<CardHeader>
						<CardTitle className="text-base">Nothing to connect</CardTitle>
						<CardDescription>
							This workspace has no per-user connections, so there is nothing
							here that would act as you.
						</CardDescription>
					</CardHeader>
				</Card>
			) : (
				<ul className="space-y-3">
					{mine.map((credential) => {
						const note = expiryNote(credential);
						return (
							<li key={credential.credential_id}>
								<Card>
									<CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
										<div className="space-y-1">
											<CardTitle className="text-base">
												{credential.credential_id}
											</CardTitle>
											<CardDescription>
												{credential.endpoint ?? credential.source}
											</CardDescription>
										</div>
										<span
											className={cn(
												"shrink-0 text-sm",
												credential.connected
													? "text-success"
													: "text-muted-foreground",
											)}
										>
											{credential.connected ? "Connected" : "Not connected"}
										</span>
									</CardHeader>
									<CardContent className="flex flex-wrap items-center justify-between gap-3">
										<p className="text-sm text-muted-foreground">
											{note ??
												(credential.connected
													? "This agent acts as you here."
													: "Not connected — this agent cannot reach your data here yet.")}
										</p>
										<div className="flex gap-2">
											<Button
												size="sm"
												variant={credential.connected ? "outline" : "default"}
												disabled={
													busy === credential.credential_id ||
													!credential.endpoint
												}
												onClick={() => void connect(credential)}
											>
												{busy === credential.credential_id
													? "Opening…"
													: credential.connected
														? "Reconnect"
														: "Connect"}
											</Button>
											{credential.connected ? (
												<Button
													size="sm"
													variant="ghost"
													disabled={busy === credential.credential_id}
													onClick={() => void disconnect(credential)}
												>
													Disconnect
												</Button>
											) : null}
										</div>
									</CardContent>
								</Card>
							</li>
						);
					})}
				</ul>
			)}

			{shared.length > 0 ? (
				<section className="space-y-2">
					<h2 className="text-sm font-medium text-muted-foreground">
						Set up for everyone
					</h2>
					<p className="text-sm text-muted-foreground">
						{shared.map((c) => c.credential_id).join(", ")} — shared connections
						your operator manages. Nothing for you to do.
					</p>
				</section>
			) : null}
		</div>
	);
}
