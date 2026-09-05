"use client";

import { CircleAlert, CircleCheck, Minus, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Card } from "@/components/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import {
	type ConnectionRow,
	type ConnectionStatus,
	INBOUND_CAPABLE,
	connectionRows,
	needsAttention,
	usersOf,
} from "@/lib/connections";
import type { WorkspaceConfig } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS: Record<ConnectionStatus, { label: string; className: string }> = {
	ready: { label: "Ready", className: "text-success" },
	"no-auth": { label: "No auth needed", className: "text-muted-foreground" },
	"needs-credential": {
		label: "Needs credential",
		className: "text-destructive",
	},
	unresolved: { label: "Not resolving", className: "text-warning" },
};

/**
 * Add or edit a credential.
 *
 * The value is never a field here. A credential is a *reference* — `source: env` naming a variable
 * — because a token typed into a browser form would be a token in devtools, in history, and in
 * every extension the person has installed. The runtime never sends one back either.
 */
function CredentialDialog({
	onClose,
	onSaved,
}: {
	onClose: () => void;
	onSaved: () => void;
}) {
	const [id, setId] = useState("");
	const [envVar, setEnvVar] = useState("");
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);

	async function save() {
		setSaving(true);
		setError(null);
		try {
			const result = await api.saveConfigEntry("credentials", id, {
				source: "env",
				config: { env: envVar },
			});
			if (!result.saved) {
				setError(
					result.errors?.map((e) => e.message).join("; ") ?? "Save failed",
				);
				return;
			}
			onSaved();
			onClose();
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setSaving(false);
		}
	}

	return (
		<Dialog open onOpenChange={onClose}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Add credential</DialogTitle>
				</DialogHeader>
				<div className="space-y-4">
					<div>
						<Label htmlFor="cred-id">Name</Label>
						<Input
							id="cred-id"
							value={id}
							onChange={(e) => setId(e.target.value)}
							placeholder="telegram-bot-token"
						/>
					</div>
					<div>
						<Label htmlFor="cred-env">Environment variable</Label>
						<Input
							id="cred-env"
							value={envVar}
							onChange={(e) => setEnvVar(e.target.value)}
							placeholder="TELEGRAM_BOT_TOKEN"
						/>
						<p className="mt-1 text-xs text-muted-foreground">
							The secret stays in your environment. SwarmKit stores the
							reference, never the value — and never sends one back to this
							page.
						</p>
					</div>
					{error && <p className="text-sm text-destructive">{error}</p>}
				</div>
				<DialogFooter>
					<Button variant="ghost" onClick={onClose}>
						Cancel
					</Button>
					<Button onClick={save} disabled={!id || !envVar || saving}>
						{saving ? "Saving…" : "Save"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

/** Add a channel — the form that replaces four steps of YAML and two environment variables. */
function ChannelDialog({
	credentials,
	onClose,
	onSaved,
}: {
	credentials: string[];
	onClose: () => void;
	onSaved: () => void;
}) {
	const [id, setId] = useState("");
	const [provider, setProvider] = useState("telegram");
	const [credentialsRef, setCredentialsRef] = useState(credentials[0] ?? "");
	const [chatId, setChatId] = useState("");
	const [inbound, setInbound] = useState(true);
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const canReceive = INBOUND_CAPABLE.includes(provider);

	async function save() {
		setSaving(true);
		setError(null);
		try {
			const value: Record<string, unknown> = {
				provider,
				credentials_ref: credentialsRef,
			};
			if (canReceive && inbound) value.inbound = true;
			if (provider === "telegram" && chatId) value.config = { chat_id: chatId };
			const result = await api.saveConfigEntry("channels", id, value);
			if (!result.saved) {
				setError(
					result.errors?.map((e) => e.message).join("; ") ?? "Save failed",
				);
				return;
			}
			onSaved();
			onClose();
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setSaving(false);
		}
	}

	return (
		<Dialog open onOpenChange={onClose}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Add channel</DialogTitle>
				</DialogHeader>
				<div className="space-y-4">
					<div>
						<Label htmlFor="ch-id">Name</Label>
						<Input
							id="ch-id"
							value={id}
							onChange={(e) => setId(e.target.value)}
							placeholder="ops"
						/>
					</div>
					<div>
						<Label htmlFor="ch-provider">Provider</Label>
						<select
							id="ch-provider"
							className="w-full rounded-md border bg-background px-3 py-2 text-sm"
							value={provider}
							onChange={(e) => setProvider(e.target.value)}
						>
							{["telegram", "discord", "slack", "webhook", "terminal"].map(
								(p) => (
									<option key={p} value={p}>
										{p}
									</option>
								),
							)}
						</select>
					</div>
					{provider !== "terminal" && (
						<div>
							<Label htmlFor="ch-cred">Credential</Label>
							<select
								id="ch-cred"
								className="w-full rounded-md border bg-background px-3 py-2 text-sm"
								value={credentialsRef}
								onChange={(e) => setCredentialsRef(e.target.value)}
							>
								{credentials.map((c) => (
									<option key={c} value={c}>
										{c}
									</option>
								))}
							</select>
						</div>
					)}
					{provider === "telegram" && (
						<div>
							<Label htmlFor="ch-chat">Chat ID</Label>
							<Input
								id="ch-chat"
								value={chatId}
								onChange={(e) => setChatId(e.target.value)}
								placeholder="-1001234567890"
							/>
							<p className="mt-1 text-xs text-muted-foreground">
								Message your bot once, then read it from <code>getUpdates</code>
								. Group ids are negative.
							</p>
						</div>
					)}
					<label
						className={cn(
							"flex items-center gap-2 text-sm",
							!canReceive && "text-muted-foreground",
						)}
					>
						<input
							type="checkbox"
							checked={canReceive && inbound}
							disabled={!canReceive}
							onChange={(e) => setInbound(e.target.checked)}
						/>
						Receive replies
						{!canReceive && (
							<span className="text-xs">
								— only Telegram can. Discord and Slack would need a gateway
								socket.
							</span>
						)}
					</label>
					{error && <p className="text-sm text-destructive">{error}</p>}
				</div>
				<DialogFooter>
					<Button variant="ghost" onClick={onClose}>
						Cancel
					</Button>
					<Button
						onClick={save}
						disabled={
							!id || saving || (provider !== "terminal" && !credentialsRef)
						}
					>
						{saving ? "Saving…" : "Save"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

export default function ConnectionsPage() {
	const [config, setConfig] = useState<WorkspaceConfig | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);
	const [dialog, setDialog] = useState<"credential" | "channel" | null>(null);

	const load = useCallback(async () => {
		try {
			setConfig(await api.workspaceConfig());
			setError(null);
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		void load();
	}, [load]);

	async function removeCredential(id: string) {
		try {
			const result = await api.deleteConfigEntry("credentials", id);
			if (!result.saved) {
				setError(
					result.errors?.map((e) => e.message).join("; ") ?? "Delete failed",
				);
				return;
			}
			await load();
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		}
	}

	const rows: ConnectionRow[] = config ? connectionRows(config) : [];
	const attention = needsAttention(rows);

	return (
		<div>
			<div className="mb-4 flex items-center justify-between">
				<div>
					<h2 className="text-xl font-bold">Connections</h2>
					<p className="text-sm text-muted-foreground">
						The servers and channels this workspace talks to, and the
						credentials they use.
					</p>
				</div>
				<div className="flex gap-2">
					<Button variant="outline" onClick={() => setDialog("credential")}>
						<Plus className="mr-1 h-4 w-4" /> Credential
					</Button>
					<Button onClick={() => setDialog("channel")}>
						<Plus className="mr-1 h-4 w-4" /> Channel
					</Button>
				</div>
			</div>

			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="mb-4 text-sm text-destructive">{error}</p>}

			{attention.length > 0 && (
				<Card className="mb-4 border-destructive/40">
					<div className="flex items-start gap-2">
						<CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
						<div className="text-sm">
							<p className="font-medium">
								{attention.length} connection{attention.length > 1 ? "s" : ""}{" "}
								will not authenticate
							</p>
							<ul className="mt-1 space-y-0.5 text-muted-foreground">
								{attention.map((r) => (
									<li key={`${r.kind}-${r.id}`}>
										<span className="font-mono">{r.id}</span> — {r.detail}
									</li>
								))}
							</ul>
						</div>
					</div>
				</Card>
			)}

			{config && rows.length === 0 && (
				<Card>
					<p className="text-sm text-muted-foreground">
						No servers or channels configured yet.
					</p>
				</Card>
			)}

			{rows.length > 0 && (
				<div className="mb-6 overflow-hidden rounded-lg border">
					<table className="w-full text-sm">
						<thead>
							<tr className="bg-muted text-muted-foreground">
								<th className="px-4 py-2 text-left font-medium">Name</th>
								<th className="px-4 py-2 text-left font-medium">Kind</th>
								<th className="px-4 py-2 text-left font-medium">Talks to</th>
								<th className="px-4 py-2 text-left font-medium">Credential</th>
								<th className="px-4 py-2 text-left font-medium">Status</th>
							</tr>
						</thead>
						<tbody>
							{rows.map((r) => (
								<tr key={`${r.kind}-${r.id}`} className="border-t">
									<td className="px-4 py-2 font-mono">{r.id}</td>
									<td className="px-4 py-2">
										{r.kind}
										{r.inbound && (
											<Badge variant="outline" className="ml-2">
												two-way
											</Badge>
										)}
									</td>
									<td
										className="max-w-xs truncate px-4 py-2 font-mono text-xs text-muted-foreground"
										title={r.target}
									>
										{r.target}
									</td>
									<td className="px-4 py-2 font-mono text-xs">
										{r.credentialId ?? <Minus className="h-3 w-3" />}
									</td>
									<td className={cn("px-4 py-2", STATUS[r.status].className)}>
										<span title={r.detail}>{STATUS[r.status].label}</span>
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}

			{config && config.credentials.length > 0 && (
				<>
					<h3 className="mb-2 text-lg font-semibold">Credentials</h3>
					<div className="overflow-hidden rounded-lg border">
						<table className="w-full text-sm">
							<thead>
								<tr className="bg-muted text-muted-foreground">
									<th className="px-4 py-2 text-left font-medium">Name</th>
									<th className="px-4 py-2 text-left font-medium">Source</th>
									<th className="px-4 py-2 text-left font-medium">Resolves</th>
									<th className="px-4 py-2 text-left font-medium">Used by</th>
									<th className="px-4 py-2" />
								</tr>
							</thead>
							<tbody>
								{config.credentials.map((c) => {
									const users = usersOf(c.id, config);
									return (
										<tr key={c.id} className="border-t">
											<td className="px-4 py-2 font-mono">{c.id}</td>
											<td className="px-4 py-2 text-muted-foreground">
												{c.source}
												{c.config.env && (
													<span className="ml-1 font-mono text-xs">
														(${c.config.env})
													</span>
												)}
											</td>
											<td className="px-4 py-2">
												{c.resolves ? (
													<span className="flex items-center gap-1 text-success">
														<CircleCheck className="h-3.5 w-3.5" /> yes
													</span>
												) : (
													<span className="flex items-center gap-1 text-warning">
														<CircleAlert className="h-3.5 w-3.5" /> no
													</span>
												)}
											</td>
											<td className="px-4 py-2 text-xs text-muted-foreground">
												{users.length ? users.join(", ") : "—"}
											</td>
											<td className="px-4 py-2 text-right">
												<Button
													variant="ghost"
													size="sm"
													title={
														users.length
															? `Used by ${users.join(", ")} — repoint those first`
															: "Delete"
													}
													disabled={users.length > 0}
													onClick={() => void removeCredential(c.id)}
												>
													<Trash2 className="h-4 w-4" />
												</Button>
											</td>
										</tr>
									);
								})}
							</tbody>
						</table>
					</div>
				</>
			)}

			{dialog === "credential" && (
				<CredentialDialog
					onClose={() => setDialog(null)}
					onSaved={() => void load()}
				/>
			)}
			{dialog === "channel" && (
				<ChannelDialog
					credentials={(config?.credentials ?? []).map((c) => c.id)}
					onClose={() => setDialog(null)}
					onSaved={() => void load()}
				/>
			)}
		</div>
	);
}
