// Placeholder script for lint / typecheck / test until the UI has source.
//
// The UI is deferred to v1.1 (design §15.3, implementation-plan M10). Until the
// v1.1 design PR lands and seed pages are added, there is nothing to lint,
// type-check, or test. This script reports that plainly and exits 0 so that
// `pnpm -r run <task>` does not spuriously fail CI.
//
// Delete this file — and restore real scripts in package.json — as part of the
// first UI implementation PR.

import { readdirSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkgRoot = resolve(here, "..");
const task = process.argv[2] ?? "check";

const sourceDirs = ["app", "components", "lib"];
let sourceFiles = 0;
for (const dir of sourceDirs) {
  const full = resolve(pkgRoot, dir);
  try {
    if (statSync(full).isDirectory()) {
      sourceFiles += readdirSync(full).length;
    }
  } catch {
    // directory doesn't exist — that's the point
  }
}

if (sourceFiles > 0) {
  console.error(
    `@swarmkit/ui: ${sourceFiles} source file(s) detected but scripts/noop.mjs is still in place. ` +
      "Restore the real lint/typecheck/test scripts in package.json.",
  );
  process.exit(1);
}

console.log(
  `@swarmkit/ui: no source yet (${task}). Restore real scripts when v1.1 UI work begins.`,
);
process.exit(0);
