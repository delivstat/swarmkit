import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata } from "next";
import "./globals.css";
import { AuthGate } from "@/components/auth-gate";
import { Sidebar } from "@/components/layout/sidebar";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
	title: "SwarmKit",
	description: "Runtime dashboard for SwarmKit workspaces",
};

export default function RootLayout({
	children,
}: {
	children: React.ReactNode;
}) {
	return (
		<html
			lang="en"
			className={cn("dark", GeistSans.variable, GeistMono.variable)}
			suppressHydrationWarning
		>
			<body className="flex h-screen overflow-hidden bg-background text-foreground antialiased">
				<AuthGate>
					<Sidebar />
					<main className="flex-1 overflow-y-auto p-6">{children}</main>
				</AuthGate>
			</body>
		</html>
	);
}
