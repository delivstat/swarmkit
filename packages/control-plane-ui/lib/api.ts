import { getAccessToken, handleUnauthorized } from "./token-store";
import type {
	AdoptResult,
	ArtifactSummary,
	ArtifactVersion,
	AuditRow,
	AuthorResult,
	CachedState,
	CanaryEnvelope,
	Command,
	Config,
	DriftRow,
	EvalRow,
	FleetIdentity,
	Gap,
	GatesEnvelope,
	Instance,
	Job,
	JoinCodeResult,
	Membership,
	MintResult,
	Observability,
	Proposal,
	RefreshResult,
	RegisterResult,
	ReviewGate,
	RunsEnvelope,
	SyncResult,
	UsageRow,
} from "./types";

/**
 * Base URL of the swarmkit-control-plane API. Configured via NEXT_PUBLIC_CONTROL_PLANE_API;
 * defaults to same-origin (relative requests) when unset — no host is hardcoded.
 */
const API_BASE = process.env.NEXT_PUBLIC_CONTROL_PLANE_API ?? "";

export class ApiError extends Error {
	constructor(
		message: string,
		readonly status: number,
	) {
		super(message);
		this.name = "ApiError";
	}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const token = getAccessToken();
	const res = await fetch(`${API_BASE}${path}`, {
		...init,
		headers: {
			"Content-Type": "application/json",
			...(token ? { Authorization: `Bearer ${token}` } : {}),
			...init?.headers,
		},
		cache: "no-store",
	});
	if (res.status === 401 && token) {
		// We sent a token and it was rejected (expired/invalid) — re-initiate login. Guarded on
		// `token` so a pre-auth request never triggers a redirect loop.
		handleUnauthorized();
	}
	if (!res.ok) {
		throw new ApiError(
			`${init?.method ?? "GET"} ${path} → ${res.status}`,
			res.status,
		);
	}
	return (await res.json()) as T;
}

export interface EnrollBody {
	name: string;
	endpoint: string;
	connection: "direct" | "poll";
	tier: string;
	token_ref?: string;
}

export const api = {
	health: () => request<{ status: string }>("/health"),
	config: () => request<Config>("/config"),
	fleetIdentity: () => request<FleetIdentity>("/fleet/identity"),
	listInstances: () => request<Instance[]>("/instances"),
	observability: () => request<Observability>("/observability"),
	usage: (instanceId?: string) =>
		request<UsageRow[]>(
			`/usage${instanceId ? `?instance_id=${instanceId}` : ""}`,
		),
	evals: (instanceId?: string) =>
		request<EvalRow[]>(
			`/eval${instanceId ? `?instance_id=${instanceId}` : ""}`,
		),
	audit: (limit = 100, instanceId?: string) =>
		request<AuditRow[]>(
			`/audit?limit=${limit}${instanceId ? `&instance_id=${instanceId}` : ""}`,
		),
	author: (id: string, message: string, topology?: string) =>
		request<AuthorResult>(`/instances/${id}/author`, {
			method: "POST",
			body: JSON.stringify({ message, ...(topology ? { topology } : {}) }),
		}),
	createProposal: (body: {
		kind: string;
		artifact_id: string;
		content: unknown;
		proposed_by?: string;
		signal?: string;
	}) =>
		request<Proposal>("/proposals", {
			method: "POST",
			body: JSON.stringify(body),
		}),
	gaps: () => request<Gap[]>("/gaps"),
	proposeFromGap: (body: {
		instance_id: string;
		capability: string;
		description?: string;
	}) =>
		request<Proposal>("/gaps/propose", {
			method: "POST",
			body: JSON.stringify(body),
		}),
	proposals: (status?: string) =>
		request<Proposal[]>(`/proposals${status ? `?status=${status}` : ""}`),
	approveProposal: (id: string) =>
		request<Proposal>(`/proposals/${id}/approve`, {
			method: "POST",
			body: "{}",
		}),
	rejectProposal: (id: string, reason: string) =>
		request<Proposal>(`/proposals/${id}/reject`, {
			method: "POST",
			body: JSON.stringify({ reason }),
		}),
	artifacts: () => request<ArtifactSummary[]>("/artifacts"),
	artifactVersions: (kind: string, id: string) =>
		request<ArtifactVersion[]>(
			`/artifacts/${kind}/${encodeURIComponent(id)}/versions`,
		),
	artifactVersion: (kind: string, id: string, version: string) =>
		request<ArtifactVersion>(
			`/artifacts/${kind}/${encodeURIComponent(id)}/versions/${version}`,
		),
	enrollInstance: (body: EnrollBody) =>
		request<Instance>("/instances", {
			method: "POST",
			body: JSON.stringify(body),
		}),
	// Mode B (design 19): mint a one-time join code to hand to a NAT'd edge instance, which
	// self-registers via `swarmkit connect --join-code`. Operator action.
	mintJoinCode: (body: { name?: string; tier?: string } = {}) =>
		request<JoinCodeResult>("/fleet/join-code", {
			method: "POST",
			body: JSON.stringify(body),
		}),
	getInstance: (id: string) => request<Instance>(`/instances/${id}`),
	deleteInstance: (id: string) =>
		request<{ deleted: string }>(`/instances/${id}`, { method: "DELETE" }),
	instanceJobs: (id: string) => request<Job[]>(`/instances/${id}/jobs`),
	// Federated per-run history (design 24) — live per-run cost detail, never stored on the panel.
	instanceRuns: (id: string) => request<RunsEnvelope>(`/instances/${id}/runs`),
	// Federated harness gates (§6.2 permission / §6.3 input) — live-pulled, resolved via the same
	// /review API the CLI + serve UI use.
	instanceGates: (id: string) =>
		request<GatesEnvelope>(`/instances/${id}/review`),
	resolveGate: (id: string, itemId: string, action: string, answer = "") =>
		request<ReviewGate>(`/instances/${id}/review/${itemId}/${action}`, {
			method: "POST",
			body: JSON.stringify({ answer }),
		}),
	// Fleet canary (design 26) — federated status + manage-scope promote/rollback.
	instanceCanary: (id: string) =>
		request<CanaryEnvelope>(`/instances/${id}/canary`),
	promoteCanary: (id: string, topology: string, version: string) =>
		request<{ promoted: boolean }>(
			`/instances/${id}/canary/${encodeURIComponent(topology)}/promote`,
			{ method: "POST", body: JSON.stringify({ version }) },
		),
	rollbackCanary: (id: string, topology: string) =>
		request<{ rolled_back: boolean }>(
			`/instances/${id}/canary/${encodeURIComponent(topology)}/rollback`,
			{ method: "POST", body: "{}" },
		),
	startCanary: (
		id: string,
		topology: string,
		body: { base_version: string; canary_version: string; weight: number },
	) =>
		request<{ started: boolean }>(
			`/instances/${id}/canary/${encodeURIComponent(topology)}/start`,
			{ method: "POST", body: JSON.stringify(body) },
		),
	// Observed-state cache (design 19 Phase 1): the last full inventory the panel pulled.
	instanceState: (id: string) => request<CachedState>(`/instances/${id}/state`),
	syncInstance: (id: string) =>
		request<SyncResult>(`/instances/${id}/sync`, { method: "POST" }),
	// Enrollment handshake (design 19, Phase 2): register with a one-time enroll token → the
	// instance issues a scoped membership credential (stored encrypted on the panel) + its state.
	registerInstance: (
		id: string,
		body: {
			enroll_token: string;
			fleet_id?: string;
			requested_scope?: string;
		},
	) =>
		request<RegisterResult>(`/instances/${id}/register`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
	refreshInstance: (id: string) =>
		request<RefreshResult>(`/instances/${id}/refresh`, { method: "POST" }),
	// The fleet relationship this panel holds for the instance (design 20).
	membership: (id: string) =>
		request<Membership>(`/instances/${id}/membership`),
	leaveFleet: (id: string) =>
		request<{ left: string; membership_id: string }>(
			`/instances/${id}/membership`,
			{ method: "DELETE" },
		),
	// Adopt a cached observed artifact into the deployable registry (design 20).
	adoptArtifact: (id: string, body: { kind: string; artifact_id: string }) =>
		request<AdoptResult>(`/instances/${id}/adopt`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
	drift: (id: string) => request<DriftRow[]>(`/instances/${id}/drift`),
	setDeployment: (
		id: string,
		kind: string,
		artifactId: string,
		version: string,
	) =>
		request<{ status: string }>(
			`/instances/${id}/deployments/${kind}/${encodeURIComponent(artifactId)}`,
			{ method: "PUT", body: JSON.stringify({ version }) },
		),
	mintToken: (id: string, body: { tier?: string; client_name?: string } = {}) =>
		request<MintResult>(`/instances/${id}/mint-token`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
	verifyInstance: (id: string) =>
		request<Instance>(`/instances/${id}/verify`, { method: "POST" }),
	listCommands: (id: string) => request<Command[]>(`/instances/${id}/commands`),
	enqueueCommand: (
		id: string,
		body: { verb: string; args?: Record<string, unknown> },
	) =>
		request<Command>(`/instances/${id}/commands`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
};
