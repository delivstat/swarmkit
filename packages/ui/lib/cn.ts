// Back-compat re-export. `cn` now lives in `lib/utils.ts` (shadcn's `@/lib/utils` convention);
// surfaces still importing `@/lib/cn` keep working until they're migrated.
export { cn } from "./utils";
