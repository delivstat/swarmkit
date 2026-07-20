/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * A reusable per-artifact quality gate: chains structured-output validation -> LLM-as-judge
 * -> optional harness review -> multi-party human approval into one composition. A
 * first-class, standalone artifact (like a skill or archetype) so the same gate can be
 * referenced by id from many nodes/stages. Every layer is optional except `approve`;
 * present layers run in fixed order (validate, judge, review, approve). The automated
 * layers filter and drive a bounded retry loop but never decide — the only exit is through
 * `approve`. The control flow is compiler-owned and fixed; a funnel configures the layers,
 * it does not rewire the graph. See design/details/gate-funnel.md.
 */
export interface SwarmKitFunnel {
    apiVersion: APIVersion;
    /**
     * Layer 4 (required): the multi-party human approval set. The only exit from the funnel to
     * `done`.
     */
    approve: Approve;
    /**
     * Layer 2: an LLM-as-judge governance decision skill that scores the artifact against a
     * rubric. Below `threshold` drives a bounded retry carrying the critique back to the
     * drafter.
     */
    judge?:     Judge;
    kind:       Kind;
    metadata:   Metadata;
    provenance: Provenance;
    /**
     * Layer 3 (optional; heavyweight gates only): an investigative harness reviewer. Findings
     * at or above `route_back_at` retry; the rest attach to the human task.
     */
    review?: Review;
    /**
     * Layer 1 (deterministic, no LLM): structured-output validation with field-specific
     * auto-correction. A shape auto-correction cannot repair is a retry — the judge never sees
     * malformed input.
     */
    validate?: Validate;
}

export type APIVersion = "swarmkit/v1";

/**
 * Layer 4 (required): the multi-party human approval set. The only exit from the funnel to
 * `done`.
 *
 * Mirrors SwarmKitApprovalPolicy (design/details/multi-party-approval.md): the rules that
 * must ALL be satisfied for the human gate to advance, plus segregation-of-duties controls.
 */
export interface Approve {
    /**
     * Default true. The identity that authored the artifact cannot approve it (segregation of
     * duties).
     */
    exclude_author?: boolean;
    /**
     * Optional four-eyes floor: at least N distinct human identities must approve across all
     * completed role-tasks.
     */
    min_distinct_approvers?: number;
    /**
     * Default reset_all. What a revision does to prior approvals: reset_all invalidates all;
     * reconfirm_changed keeps approvals whose scope was not affected.
     */
    on_revision?: OnRevision;
    /**
     * Every rule must be satisfied for the gate to advance.
     */
    rules: RuleElement[];
}

/**
 * Default reset_all. What a revision does to prior approvals: reset_all invalidates all;
 * reconfirm_changed keeps approvals whose scope was not affected.
 */
export type OnRevision = "reset_all" | "reconfirm_changed";

export interface RuleElement {
    quorum: Quorum;
    /**
     * The group of roles that may exercise this rule's scope.
     */
    roles: string[];
    /**
     * The authority exercised by this rule. Every role in `roles` must confer it (validated
     * against the role registry at load time).
     */
    scope: string;
}

/**
 * all = every role in the group must approve; any = one role suffices; {k-of: N} = any N
 * distinct role-holders.
 */
export type Quorum = QuorumClass | QuorumEnum;

export interface QuorumClass {
    "k-of": number;
}

export type QuorumEnum = "all" | "any";

/**
 * Layer 2: an LLM-as-judge governance decision skill that scores the artifact against a
 * rubric. Below `threshold` drives a bounded retry carrying the critique back to the
 * drafter.
 */
export interface Judge {
    /**
     * Default 2. Retries before the funnel escalates to a human with the last critique attached
     * — it never drops or silently advances.
     */
    max_retries?: number;
    /**
     * Path (workspace-relative) to the rubric the judge scores against.
     */
    rubric?: string;
    /**
     * Decision-skill ID (category: decision), e.g. artifact-judge.
     */
    skill: string;
    /**
     * Default 0.8. Judge score below this is a retry.
     */
    threshold?: number;
}

export type Kind = "Funnel";

export interface Metadata {
    description: string;
    id:          string;
    name:        string;
}

export interface Provenance {
    authored_by:    AuthoredBy;
    authored_date?: Date;
    registry?:      string;
    vendor?:        string;
    version:        string;
}

export type AuthoredBy = "human" | "authored_by_swarm" | "derived_from_template" | "imported_from_registry" | "vendor_published";

/**
 * Layer 3 (optional; heavyweight gates only): an investigative harness reviewer. Findings
 * at or above `route_back_at` retry; the rest attach to the human task.
 */
export interface Review {
    /**
     * Reviewer archetype ID, e.g. architect-reviewer, security-consultant.
     */
    archetype: string;
    /**
     * Read-only IAM scopes the reviewer is granted for its investigation.
     */
    read_scope?: string[];
    /**
     * Default high. Findings at or above this severity retry; lower findings attach and travel
     * to the human.
     */
    route_back_at?: RouteBackAt;
}

/**
 * Default high. Findings at or above this severity retry; lower findings attach and travel
 * to the human.
 */
export type RouteBackAt = "low" | "medium" | "high" | "critical";

/**
 * Layer 1 (deterministic, no LLM): structured-output validation with field-specific
 * auto-correction. A shape auto-correction cannot repair is a retry — the judge never sees
 * malformed input.
 */
export interface Validate {
    /**
     * Default true. Re-prompt the drafter with field-specific corrections before treating an
     * invalid shape as a retry.
     */
    autocorrect?: boolean;
    /**
     * Path (workspace-relative) to a JSON Schema the artifact must satisfy.
     */
    schema?: string;
}

