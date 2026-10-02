"use client";

import type { ConnectionRow as Row, SkillRef } from "@/lib/connections";
import { useState } from "react";

// A single provider row on /connections (design/details/dot-app.md §3.4). Shows "Used by N
// skills" per credential (SwarmKit #1007) and warns before disconnect with the top names about
// to break.
export function ConnectionRow({
	row,
	onChanged,
}: {
	row: Row;
	onChanged: () => void;
}) {
	const [busy, setBusy] = useState<"connect" | "disconnect" | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [usedByOpen, setUsedByOpen] = useState(false);

	async function connect() {
		setBusy("connect");
		setError(null);
		try {
			const res = await fetch(`/api/connections/${row.provider}/connect`, {
				method: "POST",
			});
			if (!res.ok) throw await humanError(res);
			const body = (await res.json()) as { auth_url: string };
			// Top-level navigation to Google's consent screen; after Google → runtime callback →
			// runtime issues a 302 back to /connections?connected=<provider>.
			window.location.href = body.auth_url;
		} catch (e) {
			setBusy(null);
			setError(e instanceof Error ? e.message : "connect failed");
		}
	}

	async function disconnect() {
		if (!confirmDisconnect(row.label, row.usedBy)) return;
		setBusy("disconnect");
		setError(null);
		try {
			const res = await fetch(`/api/connections/${row.provider}/disconnect`, {
				method: "POST",
			});
			if (!res.ok) throw await humanError(res);
			onChanged();
		} catch (e) {
			setError(e instanceof Error ? e.message : "disconnect failed");
		} finally {
			setBusy(null);
		}
	}

	const primaryLabel =
		row.status.state === "connected"
			? "Disconnect"
			: row.status.state === "expired"
				? "Reconnect"
				: "Connect";
	const primaryHandler =
		row.status.state === "connected" ? disconnect : connect;

	return (
		<article className="rounded-lg border border-border bg-card p-4 shadow-sm">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div className="min-w-0">
					<h3 className="text-base font-semibold">{row.label}</h3>
					<StatusLine status={row.status} />
					{row.usedBy.length > 0 && (
						<UsedByChip
							usedBy={row.usedBy}
							open={usedByOpen}
							onToggle={() => setUsedByOpen((v) => !v)}
						/>
					)}
				</div>
				<button
					type="button"
					onClick={primaryHandler}
					disabled={busy !== null}
					className={
						row.status.state === "connected"
							? "inline-flex min-h-11 items-center rounded-md border border-input bg-background px-4 text-sm font-medium hover:bg-accent disabled:opacity-60"
							: "inline-flex min-h-11 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow disabled:opacity-60"
					}
				>
					{busy ? `${primaryLabel}…` : primaryLabel}
				</button>
			</div>
			{error && (
				<p className="mt-2 text-sm text-destructive" role="alert">
					{error}
				</p>
			)}
		</article>
	);
}

function UsedByChip({
	usedBy,
	open,
	onToggle,
}: { usedBy: SkillRef[]; open: boolean; onToggle: () => void }) {
	const label = `${usedBy.length} skill${usedBy.length === 1 ? "" : "s"}`;
	return (
		<div className="mt-2">
			<button
				type="button"
				onClick={onToggle}
				aria-expanded={open}
				className="inline-flex items-center gap-1 rounded-md border border-input bg-background px-2 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
			>
				<span aria-hidden>{open ? "▾" : "▸"}</span> Used by {label}
			</button>
			{open && (
				<ul className="mt-2 space-y-0.5 pl-2 text-xs text-muted-foreground">
					{usedBy.map((s) => (
						<li key={s.id}>· {s.name}</li>
					))}
				</ul>
			)}
		</div>
	);
}

function StatusLine({ status }: { status: Row["status"] }) {
	if (status.state === "connected") {
		return (
			<p className="mt-1 text-sm text-muted-foreground">
				Connected as <span className="text-foreground">{status.owner}</span>
				{status.expiresAt && (
					<>
						{" · expires "}
						<time dateTime={status.expiresAt}>
							{new Date(status.expiresAt).toLocaleDateString()}
						</time>
					</>
				)}
			</p>
		);
	}
	if (status.state === "expired") {
		return (
			<p className="mt-1 text-sm text-destructive">
				Expired — reconnect to keep it working.
			</p>
		);
	}
	return <p className="mt-1 text-sm text-muted-foreground">Not connected.</p>;
}

function confirmDisconnect(label: string, usedBy: SkillRef[]): boolean {
	if (usedBy.length === 0) return window.confirm(`Disconnect ${label}?`);
	const top = usedBy.slice(0, 3).map((s) => s.name);
	const extra = usedBy.length > 3 ? ` and ${usedBy.length - 3} more` : "";
	return window.confirm(
		`Disconnect ${label}?\n\nThis will stop working for ${top.join(", ")}${extra}.`,
	);
}

async function humanError(res: Response): Promise<Error> {
	const body = (await res.json().catch(() => ({}))) as {
		detail?: string;
		error?: string;
	};
	return new Error(body.detail ?? body.error ?? `HTTP ${res.status}`);
}
