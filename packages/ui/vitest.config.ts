import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Mirror tsconfig's `@/*` → package-root alias so component tests can import primitives that use it.
export default defineConfig({
	resolve: {
		alias: {
			"@": fileURLToPath(new URL(".", import.meta.url)),
		},
	},
});
