import { GeistSans } from "geist/font/sans";
import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
	title: "DOT — Daily Optimization Tracker",
	description:
		"The morning brief, delegated. A SwarmKit reference app: aggregate Gmail + Calendar context, rank actionable items, delegate per-item work behind HITL gates.",
};

export const viewport: Viewport = {
	// Mobile-first: match the device width; do not scale-lock so a user can pinch-zoom if they need
	// to. The 16 px base font (see globals.css) already keeps iOS Safari from auto-zooming inputs.
	width: "device-width",
	initialScale: 1,
	themeColor: [
		{ media: "(prefers-color-scheme: dark)", color: "#0f0f0f" },
		{ media: "(prefers-color-scheme: light)", color: "#ffffff" },
	],
};

export default function RootLayout({
	children,
}: { children: React.ReactNode }) {
	// `dark` locks the neutral-dark palette. A future toggle flips this via next-themes or similar.
	return (
		<html lang="en" className={`dark ${GeistSans.className}`}>
			<body>{children}</body>
		</html>
	);
}
