import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/layout/sidebar";

export const metadata: Metadata = {
	title: "SwarmKit Dashboard",
	description: "Runtime dashboard for SwarmKit workspaces",
};

export default function RootLayout({
	children,
}: {
	children: React.ReactNode;
}) {
	return (
		<html lang="en">
			<body className="flex h-screen overflow-hidden">
				<Sidebar />
				<main className="flex-1 overflow-y-auto p-6">{children}</main>
			</body>
		</html>
	);
}
