import type { NextConfig } from "next";

const config: NextConfig = {
	// `output: "standalone"` is deliberately NOT set. In a pnpm workspace the standalone
	// tree ships empty symlinked node_modules, so `node server.js` fails with "Cannot find
	// module 'next'". The compose image uses `next start` with the real .next build + full
	// node_modules instead; keeping `output: standalone` here would print a confusing
	// warning on every `next start`.
	reactStrictMode: true,
	allowedDevOrigins: ["dots.delivstat.com"],
};

export default config;
