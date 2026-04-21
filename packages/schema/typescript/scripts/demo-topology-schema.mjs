// Demo: validate every committed topology fixture from the TS validator.
// Used by `just demo-topology-schema`.
//
// Runs without requiring a prior build — imports ajv directly and reads the
// canonical JSON Schemas from their source-of-truth location. Equivalent to
// what `@swarmkit/schema`'s public API does.

import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { parse as parseYaml } from "yaml";

const here = dirname(fileURLToPath(import.meta.url));
const SCHEMAS_ROOT = resolve(here, "..", "..", "schemas");
const FIXTURE_ROOT = resolve(here, "..", "..", "tests", "fixtures");

const topologySchema = JSON.parse(
  readFileSync(resolve(SCHEMAS_ROOT, "topology.schema.json"), "utf-8"),
);
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const validator = ajv.compile(topologySchema);

function load(kind, file) {
  return parseYaml(readFileSync(resolve(FIXTURE_ROOT, kind, file), "utf-8"));
}

function run(dir, shouldPass) {
  const files = readdirSync(resolve(FIXTURE_ROOT, dir))
    .filter((f) => f.endsWith(".yaml"))
    .sort();
  let allOk = true;
  for (const file of files) {
    const actuallyPassed = validator(load(dir, file)) === true;
    const ok = actuallyPassed === shouldPass;
    allOk = allOk && ok;
    const marker = ok ? "✓" : "✗";
    const tag = shouldPass ? "✓" : "✗";
    const note = shouldPass
      ? actuallyPassed
        ? "valid"
        : "expected valid, rejected"
      : actuallyPassed
        ? "expected invalid, passed"
        : "rejected";
    console.log(`  ${marker} ${tag} ${file}  [${note}]`);
  }
  return allOk;
}

console.log("valid fixtures:");
const validOk = run("topology", true);
console.log("invalid fixtures (should fail validation):");
const invalidOk = run("topology-invalid", false);

console.log();
const allOk = validOk && invalidOk;
console.log(allOk ? "all cases passed." : "one or more cases failed.");
process.exit(allOk ? 0 : 1);
