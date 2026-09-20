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
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import {
	type ConnectionRow,
	type ConnectionStatus,
	type RemoteAgentDraft,
	connectionRows,
	needsAttention,
	oauthCapable,
	remoteAgentRow,
	remoteAgentSkillYaml,
	suggestedAgentSkillId,
	tokenView,
	usersOf,
} from "@/lib/connections";
import type {
	A2AProbe,
	OAuthCredential,
	RemoteAgentEntry,
	WorkspaceConfig,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS: Record<ConnectionStatus, { label: string; className: string }> = {
	ready: { label: "Ready", className: "text-success" },
	"no-auth": { label: "No auth needed", className: "text-muted-foreground" },
	"needs-credential": {
		label: "Needs credential",
		className: "text-destructive",
	},
	unresolved: { label: "Not resolving", className: "text-warning" },
	// The viewer's to fix, and a healthy state — so not destructive, and not an operator problem.
	"needs-your-login": {
		label: "Connect as you",
		className: "text-muted-foreground",
	},
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

const TOKEN_TONE: Record<string, string> = {
	connected: "text-success",
	renewable: "text-muted-foreground",
	"needs-login": "text-warning",
	absent: "text-muted-foreground",
};

/**
 * The Connect button, and what a stored token looks like.
 *
 * A connection binds to the `mcp_servers` entry, not to a skill, archetype or topology
 * (`design/details/mcp-oauth.md`) — so this is a row per remote server, and every topology whose
 * agents hold skills bound to that server uses the same login.
 */
function RemoteServers({
	config,
	tokens,
	onChanged,
}: {
	config: WorkspaceConfig;
	tokens: OAuthCredential[];
	onChanged: () => void;
}) {
	const [busy, setBusy] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const remote = oauthCapable(config.mcp_servers);

	async function connect(serverId: string, endpoint: string) {
		setBusy(serverId);
		setError(null);
		try {
			// Probe first, so a server that cannot register a client says so before a window opens
			// rather than after the person has been bounced to a provider.
			const probe = await api.oauthProbe(endpoint);
			if (!probe.supported) {
				setError(probe.detail ?? "This server does not advertise OAuth.");
				return;
			}
			const { authorization_url } = await api.oauthLogin(serverId, endpoint);
			const popup = window.open(
				authorization_url,
				"swarmkit-oauth",
				"width=520,height=700",
			);
			if (!popup) {
				setError(
					"The login window was blocked. Allow pop-ups for this site and retry.",
				);
				return;
			}
			// The callback page posts back when it lands, so the table refreshes without polling.
			const onMessage = (event: MessageEvent) => {
				if (event.data?.swarmkitOAuth !== undefined) {
					window.removeEventListener("message", onMessage);
					onChanged();
				}
			};
			window.addEventListener("message", onMessage);
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setBusy(null);
		}
	}

	async function disconnect(serverId: string) {
		setBusy(serverId);
		try {
			const result = await api.oauthDisconnect(serverId);
			if (!result.deleted) setError(result.detail ?? "Nothing to disconnect.");
			onChanged();
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setBusy(null);
		}
	}

	if (remote.length === 0) return null;

	return (
		<>
			<h3 className="mb-2 mt-6 text-lg font-semibold">Remote servers</h3>
			<p className="mb-2 text-sm text-muted-foreground">
				Logging in stores a token for this server. Every topology whose agents
				hold skills bound to it uses that same connection — add a second server
				entry if you need a second identity.
			</p>
			{error && <p className="mb-2 text-sm text-destructive">{error}</p>}
			<div className="overflow-hidden rounded-lg border">
				<table className="w-full text-sm">
					<thead>
						<tr className="bg-muted text-muted-foreground">
							<th className="px-4 py-2 text-left font-medium">Server</th>
							<th className="px-4 py-2 text-left font-medium">Endpoint</th>
							<th className="px-4 py-2 text-left font-medium">Token</th>
							<th className="px-4 py-2 text-left font-medium">Scopes</th>
							<th className="px-4 py-2" />
						</tr>
					</thead>
					<tbody>
						{remote.map((server) => {
							const token =
								tokens.find((t) => t.credential_id === server.id) ?? null;
							const view = tokenView(token);
							return (
								<tr key={server.id} className="border-t">
									<td className="px-4 py-2 font-mono">{server.id}</td>
									<td
										className="max-w-xs truncate px-4 py-2 font-mono text-xs text-muted-foreground"
										title={server.endpoint}
									>
										{server.endpoint}
									</td>
									<td className={cn("px-4 py-2", TOKEN_TONE[view.health])}>
										<span title={view.detail}>{view.label}</span>
									</td>
									<td className="px-4 py-2 text-xs text-muted-foreground">
										{token?.scopes.length ? token.scopes.join(" ") : "—"}
									</td>
									<td className="px-4 py-2 text-right">
										{token ? (
											<Button
												variant="ghost"
												size="sm"
												disabled={busy === server.id}
												onClick={() => void disconnect(server.id)}
											>
												Disconnect
											</Button>
										) : (
											<Button
												size="sm"
												disabled={busy === server.id || !server.endpoint}
												onClick={() =>
													void connect(server.id, server.endpoint ?? "")
												}
											>
												{busy === server.id ? "Opening…" : "Connect"}
											</Button>
										)}
									</td>
								</tr>
							);
						})}
					</tbody>
				</table>
			</div>
		</>
	);
}

/**
 * Add a remote agent by its card URL (a2a-interop.md "Discovery" 2).
 *
 * Discovering a new agent is an authoring act, so the flow ends in a skill file, not a config
 * entry: probe the card (through the runtime — the card is cross-origin), pick one of its skills,
 * choose who answers its questions and which credential to send, and the skill is written the way
 * a catalogue bundle is imported. Nothing is granted to any agent by this; a topology names the
 * skill, or holds `pack:workspace`, to reach it.
 */
function RemoteAgentDialog({
	credentials,
	onClose,
	onSaved,
}: {
	credentials: WorkspaceConfig["credentials"];
	onClose: () => void;
	onSaved: () => void;
}) {
	const [cardUrl, setCardUrl] = useState("");
	const [probe, setProbe] = useState<A2AProbe | null>(null);
	const [probing, setProbing] = useState(false);
	const [draft, setDraft] = useState<RemoteAgentDraft | null>(null);
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);

	async function lookup() {
		setProbing(true);
		setError(null);
		setProbe(null);
		setDraft(null);
		try {
			const result = await api.a2aProbe(cardUrl.trim());
			setProbe(result);
			if (!result.supported) {
				setError(result.detail ?? "No agent card there.");
				return;
			}
			const first = result.skills?.[0];
			setDraft({
				id: suggestedAgentSkillId(first?.id ?? "", result.name ?? ""),
				name: first?.name ?? result.name ?? "",
				description: first?.description ?? result.description ?? "",
				cardUrl: cardUrl.trim(),
				skillId: first?.id ?? "",
				credentialsRef: "",
				onUnanswerable: "agent",
				permission: "cautious",
				effects: "unknown",
			});
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setProbing(false);
		}
	}

	function pickSkill(skillId: string) {
		if (!draft || !probe) return;
		const skill = probe.skills?.find((s) => s.id === skillId);
		setDraft({
			...draft,
			skillId,
			id: suggestedAgentSkillId(skillId, probe.name ?? ""),
			name: skill?.name ?? draft.name,
			description: skill?.description ?? draft.description,
		});
	}

	async function save() {
		if (!draft) return;
		setSaving(true);
		setError(null);
		try {
			const result = await api.saveSkill(draft.id, remoteAgentSkillYaml(draft));
			if (!result.valid) {
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

	const idOk = /^[a-z][a-z0-9-]*$/.test(draft?.id ?? "");

	return (
		<Dialog open onOpenChange={onClose}>
			<DialogContent className="max-w-lg">
				<DialogHeader>
					<DialogTitle>Add remote agent</DialogTitle>
				</DialogHeader>
				<div className="space-y-4">
					<div>
						<Label htmlFor="a2a-card">Agent Card URL</Label>
						<div className="flex gap-2">
							<Input
								id="a2a-card"
								value={cardUrl}
								onChange={(e) => setCardUrl(e.target.value)}
								placeholder="https://agent.example.com/.well-known/agent-card.json"
							/>
							<Button
								variant="outline"
								onClick={() => void lookup()}
								disabled={!cardUrl.trim() || probing}
							>
								{probing ? "Looking…" : "Look up"}
							</Button>
						</div>
						<p className="mt-1 text-xs text-muted-foreground">
							The runtime fetches the card and shows what the agent offers.
							Nothing is written until you add it.
						</p>
					</div>

					{probe?.supported && draft && (
						<>
							<div className="rounded-md border bg-muted/40 p-3 text-sm">
								<p className="flex items-center gap-2 font-medium">
									{probe.name}
									{probe.is_swarmkit && (
										<Badge
											variant="outline"
											title={`SwarmKit ${probe.swarmkit?.runtime ?? ""} — returns run id, usage and an observability pointer per task`}
										>
											SwarmKit
										</Badge>
									)}
								</p>
								{probe.description && (
									<p className="text-muted-foreground">{probe.description}</p>
								)}
								<p className="mt-1 font-mono text-xs text-muted-foreground">
									{probe.url}
									{probe.requires_bearer ? " · bearer token required" : ""}
								</p>
							</div>
							<div>
								<Label htmlFor="a2a-skill">Skill on the card</Label>
								<Select value={draft.skillId} onValueChange={pickSkill}>
									<SelectTrigger id="a2a-skill">
										<SelectValue placeholder="Pick a skill…" />
									</SelectTrigger>
									<SelectContent>
										{(probe.skills ?? []).map((s) => (
											<SelectItem key={s.id} value={s.id}>
												{s.name}{" "}
												<span className="font-mono text-xs">({s.id})</span>
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							</div>
							<div>
								<Label htmlFor="a2a-id">Skill id in this workspace</Label>
								<Input
									id="a2a-id"
									value={draft.id}
									onChange={(e) => setDraft({ ...draft, id: e.target.value })}
								/>
								{!idOk && (
									<p className="mt-1 text-xs text-destructive">
										lowercase letters, digits and dashes, starting with a letter
									</p>
								)}
							</div>
							<div className="grid grid-cols-2 gap-3">
								<div>
									<Label htmlFor="a2a-policy">When it asks a question</Label>
									<Select
										value={draft.onUnanswerable}
										onValueChange={(v) =>
											setDraft({
												...draft,
												onUnanswerable: v as RemoteAgentDraft["onUnanswerable"],
											})
										}
									>
										<SelectTrigger id="a2a-policy">
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											<SelectItem value="agent">
												the calling agent answers (bounded)
											</SelectItem>
											<SelectItem value="relay">a person answers</SelectItem>
											<SelectItem value="abort">the call fails</SelectItem>
										</SelectContent>
									</Select>
								</div>
								<div>
									<Label htmlFor="a2a-cred">Credential</Label>
									<Select
										value={draft.credentialsRef || "__none__"}
										onValueChange={(v) =>
											setDraft({
												...draft,
												credentialsRef: v === "__none__" ? "" : v,
											})
										}
									>
										<SelectTrigger id="a2a-cred">
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											<SelectItem value="__none__">none</SelectItem>
											{credentials.map((c) => (
												<SelectItem key={c.id} value={c.id}>
													{c.id}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
									{probe.requires_bearer && !draft.credentialsRef && (
										<p className="mt-1 text-xs text-warning">
											The card asks for a bearer token.
										</p>
									)}
								</div>
							</div>
							<div className="grid grid-cols-2 gap-3">
								<div>
									<Label htmlFor="a2a-tier">Permission tier</Label>
									<Select
										value={draft.permission}
										onValueChange={(v) =>
											setDraft({
												...draft,
												permission: v as RemoteAgentDraft["permission"],
											})
										}
									>
										<SelectTrigger id="a2a-tier">
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											<SelectItem value="open">open</SelectItem>
											<SelectItem value="cautious">cautious</SelectItem>
											<SelectItem value="strict">strict</SelectItem>
											<SelectItem value="readonly">readonly</SelectItem>
										</SelectContent>
									</Select>
								</div>
								<div>
									<Label htmlFor="a2a-effects">Effects</Label>
									<Select
										value={draft.effects}
										onValueChange={(v) =>
											setDraft({
												...draft,
												effects: v as RemoteAgentDraft["effects"],
											})
										}
									>
										<SelectTrigger id="a2a-effects">
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											<SelectItem value="read">read</SelectItem>
											<SelectItem value="write">write</SelectItem>
											<SelectItem value="unknown">unknown</SelectItem>
										</SelectContent>
									</Select>
								</div>
							</div>
						</>
					)}
					{error && <p className="text-sm text-destructive">{error}</p>}
				</div>
				<DialogFooter>
					<Button variant="ghost" onClick={onClose}>
						Cancel
					</Button>
					<Button
						onClick={() => void save()}
						disabled={!draft || !draft.skillId || !idOk || saving}
					>
						{saving ? "Adding…" : "Add agent"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

export default function ConnectionsPage() {
	const [config, setConfig] = useState<WorkspaceConfig | null>(null);
	const [tokens, setTokens] = useState<OAuthCredential[]>([]);
	const [agents, setAgents] = useState<RemoteAgentEntry[]>([]);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);
	const [dialog, setDialog] = useState<"credential" | "agent" | null>(null);

	const load = useCallback(async () => {
		try {
			const [cfg, oauth, remote] = await Promise.all([
				api.workspaceConfig(),
				api.oauthCredentials().catch(() => ({ credentials: [] })),
				api.remoteAgents().catch(() => []),
			]);
			setConfig(cfg);
			setTokens(oauth.credentials);
			setAgents(remote);
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

	const rows: ConnectionRow[] = config
		? [
				...connectionRows(config),
				...agents.map((a) => remoteAgentRow(a, config.credentials)),
			]
		: [];
	const attention = needsAttention(rows);

	return (
		<div>
			<div className="mb-4 flex items-center justify-between">
				<div>
					<h2 className="text-xl font-bold">Connections</h2>
					<p className="text-sm text-muted-foreground">
						The servers, event sinks and remote agents this workspace talks to,
						and the credentials they use.
					</p>
				</div>
				<div className="flex gap-2">
					<Button variant="outline" onClick={() => setDialog("agent")}>
						<Plus className="mr-1 h-4 w-4" /> Remote agent
					</Button>
					<Button variant="outline" onClick={() => setDialog("credential")}>
						<Plus className="mr-1 h-4 w-4" /> Credential
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
						No servers, event sinks or remote agents configured yet.
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
									<td className="px-4 py-2">{r.kind}</td>
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

			{config && (
				<RemoteServers
					config={config}
					tokens={tokens}
					onChanged={() => void load()}
				/>
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
									const users = usersOf(c.id, config, agents);
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
			{dialog === "agent" && config && (
				<RemoteAgentDialog
					credentials={config.credentials}
					onClose={() => setDialog(null)}
					onSaved={() => void load()}
				/>
			)}
		</div>
	);
}
