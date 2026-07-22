"use client";

import {
	Activity,
	Box,
	Funnel,
	GitBranch,
	Handshake,
	Inbox,
	LayoutDashboard,
	ListChecks,
	MessageCircle,
	PenTool,
	Puzzle,
	ScrollText,
	Timer,
	Users,
	Workflow,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const NAV = [
	{ href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
	{ href: "/chat", label: "Chat", icon: MessageCircle },
	{ href: "/composer", label: "Composer", icon: PenTool },
	{ href: "/jobs", label: "Jobs", icon: Activity },
	{ href: "/gates", label: "Gates", icon: Inbox },
	{ href: "/audit", label: "Audit", icon: ScrollText },
	{ href: "/topologies", label: "Topologies", icon: GitBranch },
	{ href: "/skills", label: "Skills", icon: Puzzle },
	{ href: "/archetypes", label: "Archetypes", icon: Users },
	{ href: "/funnels", label: "Funnels", icon: Funnel },
	{ href: "/pipelines", label: "Pipelines", icon: Workflow },
	{ href: "/contracts", label: "Contracts", icon: Handshake },
	{ href: "/canary", label: "Canary", icon: ListChecks },
	{ href: "/triggers", label: "Triggers", icon: Timer },
] as const;

export function Sidebar() {
	const pathname = usePathname();

	return (
		<nav className="flex w-56 shrink-0 flex-col gap-1 border-r bg-card p-3">
			<div className="mb-2 px-3 py-4">
				<h1 className="text-lg font-semibold tracking-tight">SwarmKit</h1>
				<p className="text-xs text-muted-foreground">Runtime Dashboard</p>
			</div>
			{NAV.map(({ href, label, icon: Icon }) => {
				const active = pathname === href || pathname.startsWith(`${href}/`);
				return (
					<Link
						key={href}
						href={href}
						className={cn(
							"flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
							active
								? "bg-accent font-medium text-accent-foreground"
								: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
						)}
					>
						<Icon size={16} />
						{label}
					</Link>
				);
			})}

			<div className="mt-auto flex items-center gap-1.5 px-3 py-2 text-xs text-muted-foreground">
				<Box size={14} className="opacity-50" />
				<span>v1.2.58</span>
			</div>
		</nav>
	);
}
