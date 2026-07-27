/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * A pipeline as data: an ordered set of bounded stages a controller sequences as a saga,
 * correlated by requirement_id and advanced by external events or prior-stage signals. Each
 * stage kicks a SwarmKit topology run; a stage may hold integration-contract locks, park on
 * a gate (a Funnel), and declare a compensation topology for cancellation. `loops` are
 * cross-stage edges (the defect cycle). The controller (a reference component, not the
 * runtime) owns the durable weeks-long state; SwarmKit only runs the bounded stages. See
 * design/details/pipeline-controller.md.
 */
export interface SwarmKitStageGraph {
    apiVersion: APIVersion;
    kind:       Kind;
    /**
     * Cross-stage edges (e.g. the defect cycle): an inbound event routes to a stage.
     */
    loops?:     LoopElement[];
    metadata:   Metadata;
    provenance: Provenance;
    /**
     * The pipeline's stages. Stage ids must be unique.
     */
    stages: StageElement[];
}

export type APIVersion = "swarmkit/v1";

export type Kind = "StageGraph";

export interface LoopElement {
    /**
     * The stage id this event routes to (must be a stage in this graph).
     */
    to: string;
    /**
     * The event that triggers this cross-stage edge (e.g. defect.raised).
     */
    when: string;
}

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

export interface StageElement {
    /**
     * Plan-first: the check that proves the stage's objective is met (e.g. 'SIT green +
     * contract honored'). Paired with `objective` — a stated objective without an acceptance
     * check is only half a plan.
     */
    acceptance?: string;
    /**
     * Topology run to unwind this stage if the requirement is cancelled after the stage passed.
     */
    compensation?: string;
    /**
     * A Funnel gate the stage's run parks on; the controller emits `success` when it resolves.
     */
    gate?: string;
    id:    string;
    /**
     * Integration contracts (by id) this stage holds — acquired all-or-none in fixed order
     * before the run, released on `release_locks_on`. Each references a Contract artifact
     * (design/details/contract-registry.md).
     */
    locks?: string[];
    /**
     * Plan-first (design/details/gate-coverage-and-comprehension-debt.md, slice 8): what 'done'
     * means for this stage, stated BEFORE the work — the highest-leverage comprehension edge.
     * Gate coverage flags a stage with no objective as a coverage gap; the `plan-objective`
     * decision skill judges its clarity.
     */
    objective?: string;
    /**
     * The signal whose arrival releases this stage's locks (e.g. hold the contract through
     * approval, then release).
     */
    release_locks_on?: string;
    /**
     * Optional slice-size budget for this stage's produced change
     * (design/details/gate-coverage-and-comprehension-debt.md, slice 7). Keeps vertical slices
     * small enough to review — an over-budget change should route to the funnel's review layer
     * rather than straight to human approval. Checked deterministically by `swarmkit
     * slice-check`.
     */
    slice_budget?: SliceBudget;
    /**
     * The signal emitted on clean stage completion (drives the next stage's `on`).
     */
    success?: string;
    /**
     * The topology this stage kicks as a bounded run.
     */
    topology: string;
    /**
     * Entry event(s): the stage starts when one of these arrives (external event or a prior
     * stage's signal). Named `when` (not `on`) because YAML 1.1 parsers coerce a bare `on` key
     * to a boolean.
     */
    when?: string[];
}

/**
 * Optional slice-size budget for this stage's produced change
 * (design/details/gate-coverage-and-comprehension-debt.md, slice 7). Keeps vertical slices
 * small enough to review — an over-budget change should route to the funnel's review layer
 * rather than straight to human approval. Checked deterministically by `swarmkit
 * slice-check`.
 */
export interface SliceBudget {
    /**
     * Maximum added/changed lines across the diff before the slice is over budget.
     */
    max_diff_lines?: number;
    /**
     * Maximum number of files the diff may touch before the slice is over budget.
     */
    max_files?: number;
}

