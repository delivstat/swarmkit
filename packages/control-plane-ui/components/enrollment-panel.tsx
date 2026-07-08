"use client";

import { CheckCircle2, Link2, RotateCw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Instance, RefreshResult, RegisterResult } from "@/lib/types";

const SCOPES = ["", "monitor", "manage"];

/**
 * Phase-2 enrollment handshake (design 19), Mode A. The panel registers with the instance using a
 * one-time enroll token; the instance issues a scoped membership credential that the panel stores
 * **encrypted at rest** (never shown here — unlike a serve token) and uses for its reads. "Rotate
 * key" spends the stored credential against the instance's /fleet/refresh and re-stores the new one.
 */
export function EnrollmentPanel({
	instance,
	onChanged,
}: {
	instance: Instance;
	onChanged: () => void;
}) {
	const [token, setToken] = useState("");
	const [scope, setScope] = useState("");
	const [busy, setBusy] = useState<null | "register" | "rotate">(null);
	const [registered, setRegistered] = useState<RegisterResult | null>(null);
	const [rotated, setRotated] = useState<RefreshResult | null>(null);
	const [error, setError] = useState<string | null>(null);

	async function register() {
		if (!token.trim()) {
			setError("Enter the one-time enrollment token minted on the instance.");
			return;
		}
		setBusy("register");
		setError(null);
		setRotated(null);
		try {
			const res = await api.registerInstance(instance.id, {
				enroll_token: token.trim(),
				...(scope ? { requested_scope: scope } : {}),
			});
			setRegistered(res);
			setToken("");
			onChanged();
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(null);
		}
	}

	async function rotate() {
		setBusy("rotate");
		setError(null);
		try {
			setRotated(await api.refreshInstance(instance.id));
			onChanged();
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(null);
		}
	}

	const active = rotated ?? registered;

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2">
					<Link2 className="size-4" />
					Fleet enrollment
				</CardTitle>
			</CardHeader>
			<CardContent className="space-y-4">
				<p className="text-sm text-muted-foreground">
					Register this fleet with the instance using a one-time enrollment
					token (minted on the instance: <code>POST /fleet/enroll-token</code>
					). The issued membership credential is stored{" "}
					<strong>encrypted on the panel</strong> and never shown here.
				</p>

				<div className="flex flex-wrap items-end gap-3">
					<div className="flex flex-col gap-1">
						<label
							htmlFor="enroll-token"
							className="text-xs text-muted-foreground"
						>
							Enrollment token
						</label>
						<input
							id="enroll-token"
							value={token}
							onChange={(e) => setToken(e.target.value)}
							placeholder="paste the one-time token"
							className="h-9 w-64 rounded-md border border-input bg-background px-2 font-mono text-sm"
						/>
					</div>
					<div className="flex flex-col gap-1">
						<label
							htmlFor="enroll-scope"
							className="text-xs text-muted-foreground"
						>
							Requested scope
						</label>
						<select
							id="enroll-scope"
							value={scope}
							onChange={(e) => setScope(e.target.value)}
							className="h-9 rounded-md border border-input bg-background px-2 text-sm"
						>
							{SCOPES.map((s) => (
								<option key={s || "default"} value={s}>
									{s || "(token default)"}
								</option>
							))}
						</select>
					</div>
					<Button onClick={register} disabled={busy !== null}>
						{busy === "register" ? "Registering…" : "Register"}
					</Button>
					<Button variant="outline" onClick={rotate} disabled={busy !== null}>
						<RotateCw />
						{busy === "rotate" ? "Rotating…" : "Rotate key"}
					</Button>
				</div>

				{error ? <p className="text-sm text-destructive">{error}</p> : null}

				{active ? (
					<div className="space-y-1 rounded-md border border-success/40 bg-success/5 p-4 text-sm">
						<p className="flex items-center gap-2 font-medium text-success">
							<CheckCircle2 className="size-4" />
							{rotated ? "Key rotated." : "Enrolled."} The credential is stored
							encrypted on the panel.
						</p>
						<p className="text-muted-foreground">
							Membership <code>{active.membership_id}</code> · scope{" "}
							<code>{active.scope}</code> · fingerprint{" "}
							<code>{active.fingerprint}</code>
						</p>
						{registered && !rotated ? (
							<p className="text-muted-foreground">
								Cached{" "}
								{Object.entries(registered.counts)
									.map(([k, n]) => `${n} ${k}`)
									.join(" · ")}{" "}
								· synced {registered.synced_at}
							</p>
						) : null}
					</div>
				) : null}
			</CardContent>
		</Card>
	);
}
