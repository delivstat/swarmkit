// Demo: load every valid fixture through validate() + narrow it to its
// generated TS type. Parallel of scripts/demo_codegen.py. Used by
// `just demo-codegen`.

import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { parse as parseYaml } from "yaml";

const here = dirname(fileURLToPath(import.meta.url));
const PACKAGE_ROOT = resolve(here, "..");
const SCHEMAS_DIR = resolve(PACKAGE_ROOT, "..", "schemas");
const FIXTURE_ROOT = resolve(PACKAGE_ROOT, "..", "tests", "fixtures");

const KINDS = ["topology", "skill", "archetype", "workspace", "trigger"];

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);

const validators = {};
for (const kind of KINDS) {
  const schema = JSON.parse(
    readFileSync(resolve(SCHEMAS_DIR, `${kind}.schema.json`), "utf-8"),
  );
  validators[kind] = ajv.compile(schema);
}

console.log("loading every valid fixture through validate() + typed narrow:");
let total = 0;
for (const kind of KINDS) {
  const dir = resolve(FIXTURE_ROOT, kind);
  let count = 0;
  for (const file of readdirSync(dir)
    .filter((f) => f.endsWith(".yaml"))
    .sort()) {
    const data = parseYaml(readFileSync(resolve(dir, file), "utf-8"));
    const ok = validators[kind](data) === true;
    if (!ok) {
      console.error(`  ✗ ${kind}/${file}  validate() failed`);
      process.exit(1);
    }
    count += 1;
    total += 1;
  }
  console.log(`  ✓ ${kind}: ${count} fixtures loaded OK`);
}

console.log("");
console.log(
  `typed TS round-trip — topology/from-design-doc.yaml → SwarmKitTopology`,
);
const fixture = resolve(FIXTURE_ROOT, "topology", "from-design-doc.yaml");
const data = parseYaml(readFileSync(fixture, "utf-8"));
validators.topology(data);
// In real code the cast happens after validate() returns valid; here we
// narrow by hand for the demo. Typed field access follows:
const typed = /** @type {import("../src/index.js").SwarmKitTopology} */ (data);
console.log(`  topology.metadata.name  = ${JSON.stringify(typed.metadata.name)}`);
console.log(`  topology.agents.root.id = ${JSON.stringify(typed.agents.root.id)}`);
console.log(`  topology.agents.root.role = ${JSON.stringify(typed.agents.root.role)}`);

console.log("");
console.log(`all ${total} fixtures loaded cleanly.`);
