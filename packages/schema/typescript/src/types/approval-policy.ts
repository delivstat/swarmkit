/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * Per-gate multi-party approval policy: the rules (a governance scope + a group of roles +
 * a quorum mode) that must ALL be satisfied for the gate to advance, plus
 * segregation-of-duties controls. Embedded config consumed by approval gates — not a
 * standalone artifact (no apiVersion/kind). See design/details/multi-party-approval.md.
 */
export interface SwarmKitApprovalPolicy {
    /**
     * Default true. The identity that authored/submitted the artifact cannot approve it
     * (segregation of duties).
     */
    exclude_author?: boolean;
    /**
     * Optional four-eyes floor: at least N distinct human identities must approve across all
     * completed role-tasks, regardless of how roles overlap.
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

