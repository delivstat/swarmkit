/**
 * Connections — what the portal needs to know about a workspace's servers, credentials and
 * event sinks in order to show them as something a person can set up.
 *
 * The logic here is the part worth testing: which credential a server or sink uses, whether
 * that credential actually resolves, and what a row should therefore say. The page is a rendering
 * of these answers.
 *
 * Design: `design/details/mcp-oauth.md` — a connection binds to the `mcp_servers` entry, not to a
 * skill, archetype or topology.
 *
 * A connection's credential is either `global` — one owner fixed at setup, used by every run — or
 * `per-user`, resolving to the token of whoever authenticated the run
 * (`design/details/per-caller-credential-delegation.md`). That distinction reaches this file
 * because it changes what a row can say: a global row has one status for the whole workspace, and
 * a per-user row has a status *for the person looking at it*. The same row is ready for Alice and
 * not-yet-connected for Bob, and only one of them can do anything about it.
 */

import type {
	CredentialEntry,
	EventSinkEntry,
	McpServerEntry,
	OAuthCredential,
	RemoteAgentEntry,
	WorkspaceConfig,
} from "./types";

export type ConnectionStatus =
	| "ready"
	| "needs-credential"
	| "unresolved"
	| "no-auth"
	/**
	 * A `per-user` connection the signed-in person has not connected yet.
	 *
	 * Deliberately distinct from `needs-credential` (the workspace is misconfigured — the
	 * operator's bug) and `unresolved` (configured, but the source returned nothing — the
	 * operator's environment). This one is the viewer's to fix, and it is a perfectly healthy
	 * state: every per-user connection looks like this to every person until they connect it.
	 * Collapsing the three would send people to an operator for something they can do themselves,
	 * or make a working workspace look broken to everyone who has not logged in yet.
	 */
	| "needs-your-login";

export interface ConnectionRow {
	id: string;
	kind: "server" | "event sink" | "remote agent";
	/** stdio command, http endpoint, or a sink's URL — what this actually talks to. */
	target: string;
	credentialId: string | null;
	credentialSource: string | null;
	/** `per-user` rows are per-viewer; `global` rows are the same for everybody. */
	identity: "global" | "per-user";
	status: ConnectionStatus;
	/** One sentence a person can act on. Never "error". */
	detail: string;
	permission?: string;
}

function credentialOf(
	ref: string | undefined,
	credentials: CredentialEntry[],
): CredentialEntry | null {
	if (!ref) return null;
	return credentials.find((c) => c.id === ref) ?? null;
}

/**
 * A remote server needs a credential; a local stdio one usually does not.
 *
 * This is why `no-auth` exists as a status rather than being folded into "ready": a local
 * `uvx mcp-server-git` with no credential is completely fine, and showing it as *needs setup*
 * would train people to ignore the column that matters.
 */
function serverNeedsCredential(server: McpServerEntry): boolean {
	return (
		server.transport === "http" || server.transport === "sse" || !!server.url
	);
}

/**
 * @param viewerConnected For a `per-user` credential, whether the signed-in person has connected
 * it — `undefined` when that is unknown (an operator view that has not asked, or a global row).
 */
function statusFor(
	needsCredential: boolean,
	credential: CredentialEntry | null,
	ref: string | undefined,
	viewerConnected?: boolean,
): { status: ConnectionStatus; detail: string } {
	// A per-user connection is answered by the viewer, not by the workspace: `resolves` describes
	// whether *some* owner has a token, which says nothing about whether this person does.
	if (credential && credential.identity === "per-user") {
		if (viewerConnected === false) {
			return {
				status: "needs-your-login",
				detail: `Connect your own account for "${credential.id}" — this agent uses it as you.`,
			};
		}
		if (viewerConnected === true) {
			return {
				status: "ready",
				detail: `Connected as you, through "${credential.id}".`,
			};
		}
		return {
			status: "ready",
			detail: `Each person connects "${credential.id}" as themselves.`,
		};
	}
	if (ref && !credential) {
		return {
			status: "needs-credential",
			detail: `References credential "${ref}", which is not configured.`,
		};
	}
	if (credential && !credential.resolves) {
		const where =
			credential.source === "env"
				? `$${credential.config.env ?? "?"} is not set`
				: `its ${credential.source} source returned nothing`;
		return {
			status: "unresolved",
			detail: `Credential "${credential.id}" does not resolve — ${where}.`,
		};
	}
	if (credential) {
		return { status: "ready", detail: `Using credential "${credential.id}".` };
	}
	if (needsCredential) {
		return {
			status: "needs-credential",
			detail:
				"Remote server with no credential — it will be called unauthenticated.",
		};
	}
	return { status: "no-auth", detail: "Local process, no credential needed." };
}

/** `global` unless the credential says otherwise — the default everywhere, including when a row
 * references no credential at all. */
function identityOf(credential: CredentialEntry | null): "global" | "per-user" {
	return credential?.identity === "per-user" ? "per-user" : "global";
}

export function serverRow(
	server: McpServerEntry,
	credentials: CredentialEntry[],
	viewerConnected?: Record<string, boolean>,
): ConnectionRow {
	const credential = credentialOf(server.credentials_ref, credentials);
	const { status, detail } = statusFor(
		serverNeedsCredential(server),
		credential,
		server.credentials_ref,
		credential ? viewerConnected?.[credential.id] : undefined,
	);
	return {
		id: server.id,
		kind: "server",
		target: server.endpoint ?? (server.command ?? []).join(" "),
		credentialId: credential?.id ?? server.credentials_ref ?? null,
		credentialSource: credential?.source ?? null,
		identity: identityOf(credential),
		status,
		detail,
		permission: server.permission,
	};
}

/**
 * An event sink as a connection row.
 *
 * Sinks are a plain list with no ids, so the index is the name — which is also how the runtime
 * reports what a credential is used by when it refuses a delete.
 */
export function sinkRow(
	sink: EventSinkEntry,
	index: number,
	credentials: CredentialEntry[],
): ConnectionRow {
	const credential = credentialOf(sink.credentials_ref, credentials);
	const { status, detail } = statusFor(
		sink.sink === "webhook",
		credential,
		sink.credentials_ref,
	);
	return {
		id: `events[${index}]`,
		kind: "event sink",
		target: sink.url ?? sink.sink,
		credentialId: credential?.id ?? sink.credentials_ref ?? null,
		credentialSource: credential?.source ?? null,
		identity: identityOf(credential),
		status,
		detail,
	};
}

export function connectionRows(
	config: WorkspaceConfig,
	viewerConnected?: Record<string, boolean>,
): ConnectionRow[] {
	return [
		...config.mcp_servers.map((s) =>
			serverRow(s, config.credentials, viewerConnected),
		),
		...(config.events ?? []).map((e, i) => sinkRow(e, i, config.credentials)),
	];
}

/**
 * Which servers and sinks a credential is used by.
 *
 * Shown before a delete, because the runtime refuses to remove a referenced credential and the
 * person should see why before they click rather than after.
 */
export function usersOf(
	credentialId: string,
	config: WorkspaceConfig,
	agents: RemoteAgentEntry[] = [],
): string[] {
	return [
		...agents
			.filter((a) => a.credentials_ref === credentialId)
			.map((a) => `remote agent "${a.id}"`),
		...config.mcp_servers
			.filter((s) => s.credentials_ref === credentialId)
			.map((s) => `server "${s.id}"`),
		...(config.events ?? [])
			.map((e, i) => [e, i] as const)
			.filter(([e]) => e.credentials_ref === credentialId)
			.map(([, i]) => `event sink ${i}`),
	];
}

/**
 * A remote agent (an `agent` skill with a `card_url`) as a connection row.
 *
 * Same status vocabulary as a server: a card that says it wants a bearer and a skill with no
 * `credentials_ref` reads as needs-credential, because the call will be refused over there rather
 * than here — the worse place to find out.
 */
export function remoteAgentRow(
	agent: RemoteAgentEntry,
	credentials: CredentialEntry[],
): ConnectionRow {
	const credential = credentialOf(
		agent.credentials_ref ?? undefined,
		credentials,
	);
	const { status, detail } = statusFor(
		true,
		credential,
		agent.credentials_ref ?? undefined,
	);
	return {
		id: agent.id,
		kind: "remote agent",
		target: agent.card_url,
		credentialId: credential?.id ?? agent.credentials_ref ?? null,
		credentialSource: credential?.source ?? null,
		identity: identityOf(credential),
		status,
		detail:
			status === "needs-credential" && !agent.credentials_ref
				? "Remote agent with no credential — it will be called unauthenticated."
				: detail,
		permission: agent.permission,
	};
}

export interface RemoteAgentDraft {
	id: string;
	name: string;
	description: string;
	cardUrl: string;
	skillId: string;
	credentialsRef: string;
	onUnanswerable: "agent" | "relay" | "abort";
	permission: "open" | "cautious" | "strict" | "readonly";
	effects: "read" | "write" | "unknown";
}

/** A skill id from a card skill: the card's skill id if it is a legal identifier, else derived. */
export function suggestedAgentSkillId(
	cardSkillId: string,
	cardName: string,
): string {
	const slug = (s: string) =>
		s
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-+|-+$/g, "")
			.replace(/^[^a-z]+/, "");
	return slug(cardSkillId) || slug(cardName) || "remote-agent";
}

/**
 * The skill file for a remote agent, built here rather than by the runtime so what gets written
 * is exactly what the dialog showed. Strings are quoted; nothing user-typed reaches YAML unquoted.
 */
export function remoteAgentSkillYaml(draft: RemoteAgentDraft): string {
	const q = (v: string) => JSON.stringify(v);
	const lines = [
		"apiVersion: swarmkit/v1",
		"kind: Skill",
		"metadata:",
		`  id: ${draft.id}`,
		`  name: ${q(draft.name || draft.id)}`,
		`  description: ${q(draft.description || `Calls the ${draft.name || draft.id} agent over A2A.`)}`,
		"category: capability",
		"implementation:",
		"  type: agent",
		`  card_url: ${q(draft.cardUrl)}`,
	];
	if (draft.skillId) lines.push(`  skill_id: ${q(draft.skillId)}`);
	if (draft.credentialsRef)
		lines.push(`  credentials_ref: ${q(draft.credentialsRef)}`);
	lines.push(
		`  on_unanswerable: ${draft.onUnanswerable}`,
		`  permission: ${draft.permission}`,
		`  effects: ${draft.effects}`,
		"provenance:",
		"  authored_by: human",
		`  authored_date: ${q(new Date().toISOString().slice(0, 10))}`,
		"  version: 1.0.0",
		"",
	);
	return lines.join("\n");
}

/** Rows needing a person's attention, worst first — the ordering a setup screen should use. */
export function needsAttention(rows: ConnectionRow[]): ConnectionRow[] {
	const rank: Record<ConnectionStatus, number> = {
		"needs-credential": 0,
		unresolved: 1,
		"needs-your-login": 2,
		ready: 3,
		"no-auth": 4,
	};
	// `needs-your-login` is deliberately absent: this list is the operator's "what is broken here",
	// and a per-user connection nobody has logged into yet is not broken — it is the normal state
	// of a workspace with users in it. Listing it would make a healthy deployment look unhealthy
	// in proportion to how many people use it.
	return rows
		.filter((r) => r.status === "needs-credential" || r.status === "unresolved")
		.sort((a, b) => rank[a.status] - rank[b.status]);
}

/**
 * How a stored OAuth token should read on the page.
 *
 * The distinction that matters is between *expired* and *cannot be renewed*. An expired access
 * token with a refresh token behind it is not a problem — the runtime renews it before the next
 * run without anybody being asked. Showing that as "expired, log in again" would train people to
 * re-authorise constantly for no reason, which is exactly how a real expiry gets ignored.
 */
export type TokenHealth = "connected" | "renewable" | "needs-login" | "absent";

export interface TokenView {
	health: TokenHealth;
	label: string;
	detail: string;
}

const DAY_S = 86_400;

export function tokenView(credential: OAuthCredential | null): TokenView {
	if (!credential) {
		return {
			health: "absent",
			label: "Not connected",
			detail: "No token stored. Connect to log in to this provider.",
		};
	}
	if (!credential.expired) {
		const days =
			credential.seconds_remaining === null
				? null
				: Math.floor(credential.seconds_remaining / DAY_S);
		return {
			health: "connected",
			label: "Connected",
			detail:
				days === null
					? `Connected as ${credential.owner}. No expiry reported.`
					: `Connected as ${credential.owner}. Access token valid for ${
							days > 0 ? `${days}d` : "under a day"
						}.`,
		};
	}
	if (credential.has_refresh_token) {
		return {
			health: "renewable",
			label: "Renews automatically",
			detail:
				"The access token has expired and will be renewed before the next run. No action needed.",
		};
	}
	return {
		health: "needs-login",
		label: "Log in again",
		detail:
			"The access token has expired and there is no refresh token, so it cannot be renewed.",
	};
}

/** Servers that could hold an OAuth token: remote ones. A local process has nothing to log in to. */
export function oauthCapable(servers: McpServerEntry[]): McpServerEntry[] {
	return servers.filter((s) => s.transport === "http" || !!s.endpoint);
}
