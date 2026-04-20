import { describe, expect, it } from "vitest";

import { getSchema, validate } from "../src/index.js";

describe("swarmkit-schema", () => {
  it("exposes all five canonical schemas", () => {
    for (const name of [
      "topology",
      "skill",
      "archetype",
      "workspace",
      "trigger",
    ] as const) {
      expect(getSchema(name)).toBeTypeOf("object");
    }
  });

  it("validates a minimal topology", () => {
    const result = validate("topology", {
      apiVersion: "swarmkit/v1",
      kind: "Topology",
      metadata: { name: "hello-swarm", version: "0.1.0" },
      agents: { root: { id: "root", role: "root" } },
    });
    expect(result.valid).toBe(true);
  });
});
