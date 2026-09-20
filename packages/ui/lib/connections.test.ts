import { describe, expect, it } from "vitest";

import {
	connectionRows,
	needsAttention,
	oauthCapable,
	remoteAgentRow,
	remoteAgentSkillYaml,
	serverRow,
	sinkRow,
	suggestedAgentSkillId,
	tokenView,
	usersOf,
} from "./connections";
import type {
	CredentialEntry,
	RemoteAgentEntry,
	WorkspaceConfig,
} from "./types";

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

describe("sinkRow", () => {
	it("flags a webhook sink whose credential does not resolve", () => {
		const row = sinkRow(
			{ sink: "webhook", url: "https://app/events", credentials_ref: "linear" },
			0,
			[BROKEN],
		);
		expect(row.status).toBe("unresolved");
		expect(row.id).toBe("events[0]");
	});

	it("does not ask for a credential on a stdout sink", () => {
		expect(sinkRow({ sink: "stdout" }, 1, []).status).toBe("no-auth");
	});
});

describe("usersOf", () => {
	it("lists what a credential is used by, so a delete is refused before the click", () => {
		const config: WorkspaceConfig = {
			credentials: [RESOLVING],
			mcp_servers: [{ id: "srv", credentials_ref: "tg" }],
			events: [{ sink: "webhook", url: "https://app", credentials_ref: "tg" }],
		};
		expect(usersOf("tg", config)).toEqual(['server "srv"', "event sink 0"]);
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
			events: [],
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

const REMOTE: RemoteAgentEntry = {
	id: "legal-review",
	name: "Legal review",
	card_url: "https://legal.example.com/.well-known/agent-card.json",
	skill_id: "contract-review",
	credentials_ref: "linear",
	on_unanswerable: "agent",
	permission: "strict",
	effects: "read",
	timeout_s: 600,
};

describe("remoteAgentRow", () => {
	it("is a connection row keyed on the card, with the skill's tier", () => {
		const row = remoteAgentRow({ ...REMOTE, credentials_ref: "tg" }, [
			RESOLVING,
		]);
		expect(row.kind).toBe("remote agent");
		expect(row.target).toBe(REMOTE.card_url);
		expect(row.status).toBe("ready");
		expect(row.permission).toBe("strict");
	});

	it("an unresolving credential reads as unresolved, as for a server", () => {
		expect(remoteAgentRow(REMOTE, [BROKEN]).status).toBe("unresolved");
	});

	it("no credential on a remote agent is needs-credential, said plainly", () => {
		const row = remoteAgentRow({ ...REMOTE, credentials_ref: null }, []);
		expect(row.status).toBe("needs-credential");
		expect(row.detail).toMatch(/unauthenticated/);
	});
});

describe("usersOf counts remote agents", () => {
	it("names the agent skill that references the credential", () => {
		const config: WorkspaceConfig = {
			credentials: [BROKEN],
			mcp_servers: [],
			events: [],
		};
		expect(usersOf("linear", config, [REMOTE])).toEqual([
			'remote agent "legal-review"',
		]);
	});
});

describe("suggestedAgentSkillId", () => {
	it("keeps a legal card skill id, slugs anything else", () => {
		expect(suggestedAgentSkillId("contract-review", "Legal")).toBe(
			"contract-review",
		);
		expect(suggestedAgentSkillId("Contract Review v2", "Legal")).toBe(
			"contract-review-v2",
		);
		expect(suggestedAgentSkillId("", "Legal Desk")).toBe("legal-desk");
		expect(suggestedAgentSkillId("123", "")).toBe("remote-agent");
	});
});

describe("remoteAgentSkillYaml", () => {
	it("writes exactly the agent block the dialog showed, strings quoted", () => {
		const yaml = remoteAgentSkillYaml({
			id: "legal-review",
			name: 'Legal "desk"',
			description: "",
			cardUrl: REMOTE.card_url,
			skillId: "contract-review",
			credentialsRef: "legal",
			onUnanswerable: "relay",
			permission: "strict",
			effects: "read",
		});
		expect(yaml).toContain("kind: Skill");
		expect(yaml).toContain("  id: legal-review");
		expect(yaml).toContain('  name: "Legal \\"desk\\""');
		expect(yaml).toContain("  type: agent");
		expect(yaml).toContain(`  card_url: "${REMOTE.card_url}"`);
		expect(yaml).toContain('  skill_id: "contract-review"');
		expect(yaml).toContain('  credentials_ref: "legal"');
		expect(yaml).toContain("  on_unanswerable: relay");
		expect(yaml).toContain("  permission: strict");
		expect(yaml).toContain("  effects: read");
		expect(yaml).toContain("  authored_by: human");
		// A default description is written when none was given, so the skill is not blank to a model.
		expect(yaml).toMatch(/description: "Calls the Legal/);
	});

	it("omits skill_id and credentials_ref when empty", () => {
		const yaml = remoteAgentSkillYaml({
			id: "x",
			name: "x",
			description: "d",
			cardUrl: "https://a/card",
			skillId: "",
			credentialsRef: "",
			onUnanswerable: "agent",
			permission: "cautious",
			effects: "unknown",
		});
		expect(yaml).not.toContain("skill_id");
		expect(yaml).not.toContain("credentials_ref");
	});
});

/**
 * Per-user connections: the same row says different things to different people.
 *
 * A `global` connection is a property of the workspace — one status for everybody. A `per-user`
 * one is a property of the viewer, because the token belongs to whoever is looking
 * (`design/details/per-caller-credential-delegation.md`).
 */
const PER_USER: CredentialEntry = {
	id: "calendar",
	source: "oauth",
	config: {},
	// `resolves` describes whether *some* owner has a token. For a per-user connection that says
	// nothing about the person reading the page, which is the whole point of these tests.
	resolves: true,
	identity: "per-user",
};

const CALENDAR_SERVER = {
	id: "calendar",
	transport: "http" as const,
	endpoint: "https://stub.example/mcp",
	credentials_ref: "calendar",
};

describe("per-user connections", () => {
	it("says the same row differently to two people", () => {
		const alice = serverRow(CALENDAR_SERVER, [PER_USER], { calendar: true });
		const bob = serverRow(CALENDAR_SERVER, [PER_USER], { calendar: false });

		expect(alice.status).toBe("ready");
		expect(bob.status).toBe("needs-your-login");
		expect(bob.detail).toContain("your own account");
	});

	it("keeps a global row identical for everybody", () => {
		const server = {
			id: "tg",
			transport: "http" as const,
			endpoint: "https://api.telegram.org",
			credentials_ref: "tg",
		};
		const alice = serverRow(server, [RESOLVING], { tg: true });
		const bob = serverRow(server, [RESOLVING], { tg: false });

		// A global credential's token is not the viewer's, so the viewer map must not reach it.
		expect(alice.status).toBe(bob.status);
		expect(alice.status).toBe("ready");
		expect(alice.identity).toBe("global");
	});

	it("keeps the three not-ready states distinct", () => {
		// One is the operator's bug, one the operator's environment, one the viewer's to fix. A
		// person sent to an operator for their own login gets stuck, and a workspace that looks
		// broken because nobody has logged in yet gets "fixed" by someone changing config.
		const missing = serverRow(
			{ ...CALENDAR_SERVER, credentials_ref: "absent" },
			[PER_USER],
			{},
		);
		const broken = serverRow(
			{ ...CALENDAR_SERVER, credentials_ref: "linear" },
			[BROKEN],
			{},
		);
		const mine = serverRow(CALENDAR_SERVER, [PER_USER], { calendar: false });

		expect(missing.status).toBe("needs-credential");
		expect(broken.status).toBe("unresolved");
		expect(mine.status).toBe("needs-your-login");
	});

	it("marks the row per-user so a page can say whose it is", () => {
		expect(serverRow(CALENDAR_SERVER, [PER_USER], {}).identity).toBe(
			"per-user",
		);
	});

	it("does not claim a per-user connection is ready when nobody asked the viewer", () => {
		// An operator view has no viewer state. It must not assert "connected as you" — it says
		// how the connection works instead.
		const row = serverRow(CALENDAR_SERVER, [PER_USER]);
		expect(row.status).toBe("ready");
		expect(row.detail).toContain("as themselves");
	});
});

describe("needsAttention with per-user connections", () => {
	it("does not call an unconnected per-user row broken", () => {
		// This list is the operator's "what is wrong here". A per-user connection nobody has
		// logged into is the normal state of a workspace with users in it; listing it would make
		// a healthy deployment look worse the more people use it.
		const rows = [
			serverRow(CALENDAR_SERVER, [PER_USER], { calendar: false }),
			serverRow(
				{ ...CALENDAR_SERVER, credentials_ref: "linear" },
				[BROKEN],
				{},
			),
		];
		const attention = needsAttention(rows);

		expect(attention).toHaveLength(1);
		expect(attention[0]?.status).toBe("unresolved");
	});
});
