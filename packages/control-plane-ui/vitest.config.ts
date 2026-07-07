import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
	plugins: [react()],
	resolve: {
		// Mirror the tsconfig "@/*" -> "./*" path alias so tests import like app code.
		alias: { "@": path.resolve(__dirname) },
	},
	test: {
		environment: "jsdom",
		globals: true,
		setupFiles: ["./vitest.setup.ts"],
		// Only our unit tests; Playwright owns e2e/.
		include: ["**/*.test.{ts,tsx}"],
		exclude: ["e2e/**", "node_modules/**", ".next/**"],
	},
});
