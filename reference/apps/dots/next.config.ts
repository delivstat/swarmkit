import type { NextConfig } from "next";

const config: NextConfig = {
	output: "standalone",
	reactStrictMode: true,
	allowedDevOrigins: ["dots.delivstat.com"],
};

export default config;
