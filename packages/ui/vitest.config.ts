import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Mirror tsconfig's `@/*` → package-root alias so component tests can import primitives that use it.
export default defineConfig({
	// tsconfig says `jsx: preserve` (Next compiles it); tests render components, so esbuild must.
	esbuild: { jsx: "automatic" },
	resolve: {
		alias: {
			"@": fileURLToPath(new URL(".", import.meta.url)),
		},
	},
});
