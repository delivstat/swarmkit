import type { NextConfig } from "next";

// Static export: the portal is a pure client SPA served by `swarmkit serve` (no Node at runtime).
// `images.unoptimized` because export has no image optimiser; `trailingSlash` so each route is a
// directory with an index.html (clean static hosting + a simpler serve mount).
const config: NextConfig = {
	output: "export",
	trailingSlash: true,
	images: { unoptimized: true },
};

export default config;
