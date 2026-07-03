import path from "node:path";
import type { NextConfig } from "next";

const config: NextConfig = {
	// Standalone output: a self-contained server bundle for the container image
	// (deploy/control-plane/Dockerfile.ui). outputFileTracingRoot points at the
	// monorepo root so pnpm-workspace deps are traced correctly.
	output: "standalone",
	outputFileTracingRoot: path.resolve(process.cwd(), "../.."),
};

export default config;
