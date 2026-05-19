// Demo: validate every committed fixture for a given schema and print
// pass/fail. Parallel of scripts/demo_schema.py. Used by
// `just demo-<artifact>-schema`.
//
// Usage:  node demo-schema.mjs <artifact>
//   where <artifact> is topology | skill | archetype | workspace | trigger.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { parse as parseYaml } from "yaml";

const ALLOWED = ["topology", "skill", "archetype", "workspace", "trigger"];

const here = dirname(fileURLToPath(import.meta.url));
const SCHEMAS_ROOT = resolve(here, "..", "..", "schemas");
const FIXTURE_ROOT = resolve(here, "..", "..", "tests", "fixtures");

const schema = process.argv[2];
if (!ALLOWED.includes(schema)) {
  console.error(`usage: demo-schema.mjs <${ALLOWED.join(" | ")}>`);
  process.exit(2);
}

const schemaJson = JSON.parse(
  readFileSync(resolve(SCHEMAS_ROOT, `${schema}.schema.json`), "utf-8"),
);
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const validator = ajv.compile(schemaJson);

function load(kind, file) {
  return parseYaml(readFileSync(resolve(FIXTURE_ROOT, kind, file), "utf-8"));
}

function exists(p) {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function run(dir, shouldPass) {
  if (!exists(resolve(FIXTURE_ROOT, dir))) return true;
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

console.log(`valid fixtures (${schema}):`);
const validOk = run(schema, true);
console.log(`invalid fixtures (${schema}) — should fail validation:`);
const invalidOk = run(`${schema}-invalid`, false);

console.log();
const allOk = validOk && invalidOk;
console.log(allOk ? "all cases passed." : "one or more cases failed.");
process.exit(allOk ? 0 : 1);
