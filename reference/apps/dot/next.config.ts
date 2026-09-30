import type { NextConfig } from "next";

// `standalone` produces a minimal Node bundle at .next/standalone that the Dockerfile's `run`
// stage copies into the shipped image. Unlike the SwarmKit portal (which is a pure client SPA and
// static-exports), DOT runs a server process — for OAuth session cookies, the API-proxy layer
// against the runtime, and SSE endpoints for streaming per-item runs. See design/details/dot-app.md.
const config: NextConfig = {
	output: "standalone",
};

export default config;
