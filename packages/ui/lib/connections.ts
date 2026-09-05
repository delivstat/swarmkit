/**
 * Connections — what the portal needs to know about a workspace's servers, credentials and
 * channels in order to show them as something a person can set up.
 *
 * The logic here is the part worth testing: which credential a server or channel uses, whether
 * that credential actually resolves, and what a row should therefore say. The page is a rendering
 * of these answers.
 *
 * Design: `design/details/mcp-oauth.md` — a connection binds to the `mcp_servers` entry, not to a
 * skill, archetype or topology, and its credential has one owner fixed at setup.
 */

import type {
	ChannelEntry,
	CredentialEntry,
	McpServerEntry,
	WorkspaceConfig,
} from "./types";

/** Providers a human can answer on. Mirrors `INBOUND_CAPABLE` in the runtime. */
export const INBOUND_CAPABLE = ["telegram"];

export type ConnectionStatus =
	| "ready"
	| "needs-credential"
	| "unresolved"
	| "no-auth";

export interface ConnectionRow {
	id: string;
	kind: "server" | "channel";
	/** stdio command, http url, or the channel's provider — what this actually talks to. */
	target: string;
	credentialId: string | null;
	credentialSource: string | null;
	status: ConnectionStatus;
	/** One sentence a person can act on. Never "error". */
	detail: string;
	permission?: string;
	inbound?: boolean;
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

function statusFor(
	needsCredential: boolean,
	credential: CredentialEntry | null,
	ref: string | undefined,
): { status: ConnectionStatus; detail: string } {
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

export function serverRow(
	server: McpServerEntry,
	credentials: CredentialEntry[],
): ConnectionRow {
	const credential = credentialOf(server.credentials_ref, credentials);
	const { status, detail } = statusFor(
		serverNeedsCredential(server),
		credential,
		server.credentials_ref,
	);
	return {
		id: server.id,
		kind: "server",
		target: server.endpoint ?? (server.command ?? []).join(" "),
		credentialId: credential?.id ?? server.credentials_ref ?? null,
		credentialSource: credential?.source ?? null,
		status,
		detail,
		permission: server.permission,
	};
}

export function channelRow(
	channel: ChannelEntry,
	credentials: CredentialEntry[],
): ConnectionRow {
	const credential = credentialOf(channel.credentials_ref, credentials);
	// `terminal` prints to the serve process's stdout; there is nothing to authenticate.
	const needs = channel.provider !== "terminal";
	const { status, detail } = statusFor(
		needs,
		credential,
		channel.credentials_ref,
	);
	return {
		id: channel.id,
		kind: "channel",
		target: channel.provider,
		credentialId: credential?.id ?? channel.credentials_ref ?? null,
		credentialSource: credential?.source ?? null,
		status,
		detail,
		inbound: !!channel.inbound && INBOUND_CAPABLE.includes(channel.provider),
	};
}

export function connectionRows(config: WorkspaceConfig): ConnectionRow[] {
	return [
		...config.mcp_servers.map((s) => serverRow(s, config.credentials)),
		...config.channels.map((c) => channelRow(c, config.credentials)),
	];
}

/**
 * Which servers and channels a credential is used by.
 *
 * Shown before a delete, because the runtime refuses to remove a referenced credential and the
 * person should see why before they click rather than after.
 */
export function usersOf(
	credentialId: string,
	config: WorkspaceConfig,
): string[] {
	return [
		...config.mcp_servers
			.filter((s) => s.credentials_ref === credentialId)
			.map((s) => `server "${s.id}"`),
		...config.channels
			.filter((c) => c.credentials_ref === credentialId)
			.map((c) => `channel "${c.id}"`),
	];
}

/** Rows needing a person's attention, worst first — the ordering a setup screen should use. */
export function needsAttention(rows: ConnectionRow[]): ConnectionRow[] {
	const rank: Record<ConnectionStatus, number> = {
		"needs-credential": 0,
		unresolved: 1,
		ready: 2,
		"no-auth": 3,
	};
	return rows
		.filter((r) => r.status === "needs-credential" || r.status === "unresolved")
		.sort((a, b) => rank[a.status] - rank[b.status]);
}
