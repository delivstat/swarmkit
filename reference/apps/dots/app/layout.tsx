import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
	title: "dots",
	description: "Multi-Dot CopilotKit + AG-UI reference app for SwarmKit.",
};

export default function RootLayout({
	children,
}: {
	children: React.ReactNode;
}): React.ReactElement {
	return (
		<html lang="en" data-theme="dark" className={`${GeistSans.variable} ${GeistMono.variable}`}>
			<body className="min-h-screen bg-background text-foreground antialiased">{children}</body>
		</html>
	);
}
