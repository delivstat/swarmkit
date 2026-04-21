// Generate TypeScript types from the canonical JSON Schemas using quicktype.
// Mirror of scripts/codegen_pydantic.py.
//
// The canonical JSON Schemas are the source of truth (see
// docs/notes/schema-change-discipline.md). Generated types live under
// packages/schema/typescript/src/types/ and must not be hand-edited.
//
// Regenerate with: just schema-codegen-ts
// CI runs a drift check — uncommitted regenerated output fails the build.
//
// Chose quicktype-core over json-schema-to-typescript because the latter
// trips on recursive $defs (our topology's `agent` → `children` → `child_agent`
// → `agent` loop). quicktype handles JSON Schema 2020-12 including $defs and
// recursive refs cleanly.

import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  InputData,
  JSONSchemaInput,
  FetchingJSONSchemaStore,
  quicktype,
} from "quicktype-core";

const here = dirname(fileURLToPath(import.meta.url));
const PACKAGE_ROOT = resolve(here, "..");
const SCHEMAS_DIR = resolve(PACKAGE_ROOT, "..", "schemas");
const OUTPUT_DIR = resolve(PACKAGE_ROOT, "src", "types");

// artifact → root-type name. Must match `title` in each .schema.json.
const ARTIFACTS = {
  topology: "SwarmKitTopology",
  skill: "SwarmKitSkill",
  archetype: "SwarmKitArchetype",
  workspace: "SwarmKitWorkspace",
  trigger: "SwarmKitTrigger",
};

// quicktype names recursive leaf types as "<root-name minus last character>"
// (its de-duplication heuristic), producing awkward names like
// `SwarmKitTopolog` and `SwarmKitSkil`. Rename them to meaningful domain
// names. Only applied to schemas that contain recursive $defs.
const RECURSIVE_RENAMES = {
  topology: { SwarmKitTopolog: "ChildAgent" },
  skill: { SwarmKitSkil: "FieldSpec" },
};

const HEADER = `/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
`;

async function generateOne(artifact, rootName) {
  const schemaPath = resolve(SCHEMAS_DIR, `${artifact}.schema.json`);
  const schemaSource = readFileSync(schemaPath, "utf-8");

  const schemaInput = new JSONSchemaInput(new FetchingJSONSchemaStore());
  await schemaInput.addSource({ name: rootName, schema: schemaSource });

  const inputData = new InputData();
  inputData.addInput(schemaInput);

  const result = await quicktype({
    inputData,
    lang: "ts",
    rendererOptions: {
      "just-types": "true",
      "nice-property-names": "false",
      "explicit-unions": "true",
      "prefer-unions": "true",
    },
  });

  let body = result.lines.join("\n");
  const renames = RECURSIVE_RENAMES[artifact] ?? {};
  for (const [from, to] of Object.entries(renames)) {
    // Replace all whole-word occurrences of the awkward name with the
    // domain name. Word-boundary anchors prevent collisions with prefixes
    // (the root name itself is a superstring and shouldn't match).
    body = body.replace(new RegExp(`\\b${from}\\b`, "g"), to);
  }

  const outPath = resolve(OUTPUT_DIR, `${artifact}.ts`);
  writeFileSync(outPath, HEADER + body + "\n", "utf-8");
  console.log(`  ▶ ${artifact}`);
}

function writeIndex() {
  const lines = [
    "/* eslint-disable */",
    "// Generated package — do not edit by hand. Regenerate with:",
    "//   just schema-codegen-ts",
    "",
  ];
  for (const [artifact, root] of Object.entries(ARTIFACTS)) {
    lines.push(`export type { ${root} } from "./${artifact}.js";`);
  }
  lines.push("");
  writeFileSync(resolve(OUTPUT_DIR, "index.ts"), lines.join("\n"), "utf-8");
}

async function main() {
  rmSync(OUTPUT_DIR, { recursive: true, force: true });
  mkdirSync(OUTPUT_DIR, { recursive: true });
  const rel = OUTPUT_DIR.slice(OUTPUT_DIR.indexOf("packages/"));
  console.log(`generating TS types into ${rel}`);
  for (const [artifact, rootName] of Object.entries(ARTIFACTS)) {
    await generateOne(artifact, rootName);
  }
  writeIndex();
  console.log("done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
