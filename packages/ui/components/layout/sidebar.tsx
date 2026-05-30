"use client";

import { cn } from "@/lib/cn";
import {
	Activity,
	Box,
	GitBranch,
	LayoutDashboard,
	ListChecks,
	MessageCircle,
	PenTool,
	Puzzle,
	Timer,
	Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
	{ href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
	{ href: "/chat", label: "Chat", icon: MessageCircle },
	{ href: "/composer", label: "Composer", icon: PenTool },
	{ href: "/jobs", label: "Jobs", icon: Activity },
	{ href: "/topologies", label: "Topologies", icon: GitBranch },
	{ href: "/skills", label: "Skills", icon: Puzzle },
	{ href: "/archetypes", label: "Archetypes", icon: Users },
	{ href: "/canary", label: "Canary", icon: ListChecks },
	{ href: "/triggers", label: "Triggers", icon: Timer },
] as const;

export function Sidebar() {
	const pathname = usePathname();

	return (
		<nav
			className="flex flex-col gap-1 p-3 w-56 shrink-0 border-r"
			style={{ background: "var(--bg-sidebar)", borderColor: "var(--border)" }}
		>
			<div className="px-3 py-4 mb-2">
				<h1 className="text-lg font-bold tracking-tight">SwarmKit</h1>
				<p className="text-xs" style={{ color: "var(--fg-muted)" }}>
					Runtime Dashboard
				</p>
			</div>
			{NAV.map(({ href, label, icon: Icon }) => {
				const active = pathname === href || pathname.startsWith(`${href}/`);
				return (
					<Link
						key={href}
						href={href}
						className={cn(
							"flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors",
							active ? "font-medium" : "opacity-70 hover:opacity-100",
						)}
						style={
							active ? { background: "var(--border)", color: "var(--fg)" } : {}
						}
					>
						<Icon size={16} />
						{label}
					</Link>
				);
			})}

			<div className="mt-auto px-3 py-2">
				<Box size={14} className="inline mr-1.5 opacity-50" />
				<span className="text-xs" style={{ color: "var(--fg-muted)" }}>
					v1.2.58
				</span>
			</div>
		</nav>
	);
}
