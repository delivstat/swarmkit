// Copy canonical schemas from ../schemas into ./schemas so the published npm
// package is self-contained. Run as part of `pnpm build`.
//
// Why copy rather than symlink? npm tarballs don't follow symlinks reliably
// across platforms. The source of truth stays in packages/schema/schemas.

import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../../schemas");
const dest = resolve(here, "../schemas");

if (!existsSync(src)) {
  throw new Error(`Canonical schema dir not found: ${src}`);
}

if (existsSync(dest)) rmSync(dest, { recursive: true });
mkdirSync(dest, { recursive: true });
cpSync(src, dest, { recursive: true });
console.log(`Copied schemas: ${src} → ${dest}`);
