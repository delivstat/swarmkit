import type { Metadata } from "next";

import { AppSidebar } from "@/components/app-sidebar";
import { AuthGate } from "@/components/auth-gate";
import "./globals.css";

export const metadata: Metadata = {
	title: "SwarmKit Fleet",
	description: "Control panel for managing multiple SwarmKit instances.",
};

export default function RootLayout({
	children,
}: { children: React.ReactNode }) {
	return (
		<html lang="en" className="dark">
			<body className="antialiased">
				<AuthGate>
					<div className="flex">
						<AppSidebar />
						<main className="h-screen flex-1 overflow-y-auto">{children}</main>
					</div>
				</AuthGate>
			</body>
		</html>
	);
}
