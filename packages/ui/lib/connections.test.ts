import { describe, expect, it } from "vitest";

import {
	channelRow,
	connectionRows,
	needsAttention,
	oauthCapable,
	serverRow,
	tokenView,
	usersOf,
} from "./connections";
import type { CredentialEntry, WorkspaceConfig } from "./types";

const RESOLVING: CredentialEntry = {
	id: "tg",
	source: "env",
	config: { env: "TELEGRAM_BOT_TOKEN" },
	resolves: true,
};
const BROKEN: CredentialEntry = {
	id: "linear",
	source: "env",
	config: { env: "LINEAR_TOKEN" },
	resolves: false,
};

describe("serverRow", () => {
	it("treats a local stdio server with no credential as fine, not as unfinished setup", () => {
		// Folding this into "needs setup" would light up every local server and train people to
		// ignore the one column that matters.
		const row = serverRow(
			{ id: "git", transport: "stdio", command: ["uvx", "mcp-server-git"] },
			[],
		);
		expect(row.status).toBe("no-auth");
		expect(row.detail).toMatch(/no credential needed/);
	});

	it("flags a remote server with no credential", () => {
		const row = serverRow(
			{
				id: "linear",
				transport: "http",
				endpoint: "https://mcp.linear.app/mcp",
			},
			[],
		);
		expect(row.status).toBe("needs-credential");
		expect(row.detail).toMatch(/unauthenticated/);
	});

	it("names the missing credential when the reference dangles", () => {
		const row = serverRow(
			{
				id: "linear",
				transport: "http",
				endpoint: "https://x/mcp",
				credentials_ref: "gone",
			},
			[],
		);
		expect(row.status).toBe("needs-credential");
		expect(row.detail).toContain('"gone"');
	});

	it("says which variable is unset when a credential does not resolve", () => {
		// The whole point of the `resolves` field: an unexported env var is invisible everywhere
		// else and surfaces much later as a platform auth error.
		const row = serverRow(
			{
				id: "linear",
				transport: "http",
				endpoint: "https://x/mcp",
				credentials_ref: "linear",
			},
			[BROKEN],
		);
		expect(row.status).toBe("unresolved");
		expect(row.detail).toContain("$LINEAR_TOKEN is not set");
	});

	it("is ready when the credential resolves", () => {
		const row = serverRow(
			{
				id: "tgsrv",
				transport: "http",
				endpoint: "https://x/mcp",
				credentials_ref: "tg",
			},
			[RESOLVING],
		);
		expect(row.status).toBe("ready");
	});

	it("shows the command for stdio and the url for http", () => {
		expect(
			serverRow({ id: "a", transport: "stdio", command: ["uvx", "x"] }, [])
				.target,
		).toBe("uvx x");
		expect(
			serverRow({ id: "b", transport: "http", endpoint: "https://x/mcp" }, [])
				.target,
		).toBe("https://x/mcp");
	});
});

describe("channelRow", () => {
	it("marks inbound only where the provider can actually receive", () => {
		expect(
			channelRow(
				{
					id: "ops",
					provider: "telegram",
					credentials_ref: "tg",
					inbound: true,
				},
				[RESOLVING],
			).inbound,
		).toBe(true);
		// Discord cannot receive here; claiming otherwise would promise replies that never come.
		expect(
			channelRow(
				{
					id: "eng",
					provider: "discord",
					credentials_ref: "tg",
					inbound: true,
				},
				[RESOLVING],
			).inbound,
		).toBe(false);
	});

	it("does not ask for a credential on the terminal channel", () => {
		expect(channelRow({ id: "console", provider: "terminal" }, []).status).toBe(
			"no-auth",
		);
	});
});

describe("usersOf", () => {
	it("lists what a credential is used by, so a delete is refused before the click", () => {
		const config: WorkspaceConfig = {
			credentials: [RESOLVING],
			mcp_servers: [{ id: "srv", credentials_ref: "tg" }],
			channels: [{ id: "ops", provider: "telegram", credentials_ref: "tg" }],
		};
		expect(usersOf("tg", config)).toEqual(['server "srv"', 'channel "ops"']);
		expect(usersOf("other", config)).toEqual([]);
	});
});

describe("needsAttention", () => {
	it("keeps only what a person must act on, missing credentials first", () => {
		const config: WorkspaceConfig = {
			credentials: [BROKEN],
			mcp_servers: [
				{ id: "local", transport: "stdio", command: ["x"] },
				{
					id: "broken",
					transport: "http",
					endpoint: "https://x",
					credentials_ref: "linear",
				},
				{
					id: "dangling",
					transport: "http",
					endpoint: "https://y",
					credentials_ref: "gone",
				},
			],
			channels: [],
		};
		const rows = needsAttention(connectionRows(config));
		expect(rows.map((r) => r.id)).toEqual(["dangling", "broken"]);
	});
});

describe("tokenView", () => {
	const base = {
		credential_id: "linear",
		owner: "srijith@delivstat.com",
		provider: "https://auth.linear.app",
		endpoint: "https://mcp.linear.app/mcp",
		scopes: ["read"],
		has_refresh_token: true,
		refreshed_at: null,
	};

	it("reports no token as not connected", () => {
		expect(tokenView(null).health).toBe("absent");
	});

	it("shows a live token as connected, naming the owner", () => {
		const view = tokenView({
			...base,
			expires_at: 1,
			seconds_remaining: 3 * 86_400,
			expired: false,
		});
		expect(view.health).toBe("connected");
		expect(view.detail).toContain("srijith@delivstat.com");
		expect(view.detail).toContain("3d");
	});

	it("does not cry wolf when an expired token can be renewed", () => {
		// The distinction that matters. Showing "expired, log in again" for a token the runtime
		// renews on its own trains people to re-authorise for nothing — which is how a real
		// expiry gets ignored.
		const view = tokenView({
			...base,
			expires_at: 1,
			seconds_remaining: -10,
			expired: true,
			has_refresh_token: true,
		});
		expect(view.health).toBe("renewable");
		expect(view.detail).toMatch(/No action needed/);
	});

	it("asks for a login only when there is no refresh token", () => {
		const view = tokenView({
			...base,
			expires_at: 1,
			seconds_remaining: -10,
			expired: true,
			has_refresh_token: false,
		});
		expect(view.health).toBe("needs-login");
	});
});

describe("oauthCapable", () => {
	it("keeps remote servers only — a local process has nothing to log in to", () => {
		const servers = oauthCapable([
			{ id: "git", transport: "stdio", command: ["uvx", "x"] },
			{
				id: "linear",
				transport: "http",
				endpoint: "https://mcp.linear.app/mcp",
			},
		]);
		expect(servers.map((s) => s.id)).toEqual(["linear"]);
	});
});
