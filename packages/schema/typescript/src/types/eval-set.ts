/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * An eval set: the cases a topology is scored against by `swarmkit eval` and the serve's
 * eval routes. Each case runs the target topology on its input and passes when every
 * expectation set on it passes; a case with no expectations is a smoke test that passes
 * when the run completes. The test gate of growth through authoring (§12). Case ids must be
 * unique within a set and the target must name a topology in the workspace — both checked
 * by the runtime, since JSON Schema cannot express them. See
 * design/details/eval-set-schema-v1.md.
 */
export interface SwarmKitEvalSet {
    apiVersion: APIVersion;
    /**
     * The cases to run, in order. Reports and comparisons between runs are keyed by case id.
     */
    cases:       Case[];
    kind:        Kind;
    metadata:    Metadata;
    provenance?: Provenance;
    /**
     * The topology every case runs against. `name@version` pins one version (for example a
     * canary candidate); a bare name runs the workspace's current version. Cases are never
     * routed through a canary split, so a report always scores one version.
     */
    target: string;
}

export type APIVersion = "swarmkit/v1";

export interface Case {
    expect?: Expectations;
    /**
     * Unique within the set. A case keeps its id across edits so its results stay comparable
     * from run to run.
     */
    id: string;
    /**
     * What the target topology is run on — the same text a caller would send to `POST /run`.
     */
    input:   string;
    source?: CapturedFrom;
}

/**
 * Checks on the run's output. The case passes when every check set here passes.
 * Deterministic checks cost nothing; `judge`, `rubric` and `reference` each make one model
 * call.
 */
export interface Expectations {
    /**
     * Each string must appear in the output (case-insensitive).
     */
    contains?: string[];
    /**
     * The whole output, after trimming whitespace, must equal this. Model output varies, so
     * prefer the other checks unless the topology is deterministic.
     */
    equals?: string;
    /**
     * A decision skill that scores the output. Passes when its verdict is `pass` at
     * `min_confidence` or above.
     */
    judge?: string;
    /**
     * The confidence `judge`, `rubric` and `reference` must reach to pass.
     */
    min_confidence?: number;
    /**
     * No string may appear in the output (case-insensitive).
     */
    not_contains?: string[];
    /**
     * The output must contain something other than whitespace.
     */
    not_empty?: boolean;
    /**
     * A known-good answer, usually a recorded output captured from a past run. The workspace's
     * judge model decides whether the new output gives the same answer (wording may differ).
     * The reference is passed to the judge as data, never as instructions. Passes like `judge`.
     */
    reference?: string;
    /**
     * A Python regular expression the output must match somewhere (`re.search`).
     */
    regex?: string;
    /**
     * An inline rubric the workspace's judge model scores the output against — no skill needed.
     * Passes like `judge`.
     */
    rubric?: string;
    /**
     * Skills the run must have invoked, read from its events.
     */
    used_skills?: string[];
}

/**
 * The recorded run this case was captured from by `swarmkit eval-capture` or `POST
 * /evals/capture`. Informational: the runner never reads it, and capture uses `run_id` to
 * skip a run it has already captured. See design/details/eval-capture.md.
 */
export interface CapturedFrom {
    /**
     * When the case was captured (ISO 8601).
     */
    captured_at?: Date;
    /**
     * The id of the job the case was captured from.
     */
    run_id: string;
    /**
     * The version of the target topology that produced the recorded output.
     */
    topology_version?: string;
}

export type Kind = "EvalSet";

export interface Metadata {
    /**
     * What this set checks, for the person reviewing it.
     */
    description?: string;
    /**
     * The set's id — what `swarmkit eval <workspace> <id>` and the serve's `/evals/<id>` routes
     * name.
     */
    id: string;
    /**
     * A display name. Defaults to the id.
     */
    name?: string;
}

export interface Provenance {
    authored_by:    AuthoredBy;
    authored_date?: Date;
    registry?:      string;
    vendor?:        string;
    version:        string;
}

export type AuthoredBy = "human" | "authored_by_swarm" | "derived_from_template" | "imported_from_registry" | "vendor_published";

