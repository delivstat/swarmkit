"use client";

import {
	Activity,
	Box,
	BrainCircuit,
	Database,
	Funnel,
	Gauge,
	GitBranch,
	Handshake,
	Inbox,
	LayoutDashboard,
	ListChecks,
	MessageCircle,
	PenTool,
	PlayCircle,
	Puzzle,
	ScrollText,
	Timer,
	Users,
	Workflow,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

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
	{ href: "/runs", label: "Runs", icon: PlayCircle },
	{ href: "/contracts", label: "Contracts", icon: Handshake },
	{ href: "/comprehension", label: "Comprehension", icon: Gauge },
	{ href: "/memory", label: "Memory", icon: BrainCircuit },
	{ href: "/canary", label: "Canary", icon: ListChecks },
	{ href: "/triggers", label: "Triggers", icon: Timer },
	{ href: "/system", label: "System", icon: Database },
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

			<VersionFooter />
		</nav>
	);
}

/** Both component versions, read from the server.
 *
 * This footer used to be a hardcoded `v1.2.58` — a literal for neither package, left behind when
 * the runtime was at 1.2.x. It also showed ONE number, which cannot be right: `swarmkit serve` is
 * the runtime hosting a separately versioned portal, and a mismatch between them is exactly what a
 * reader needs to see (an old portal paired with a new runtime is a real and silent failure mode).
 */
function VersionFooter() {
	const [health, setHealth] = useState<HealthResponse | null>(null);

	useEffect(() => {
		api
			.health()
			.then(setHealth)
			.catch(() => setHealth(null));
	}, []);

	const runtime = health?.runtime_version;
	const webui = health?.webui_version;

	return (
		<div className="mt-auto flex flex-col gap-0.5 px-3 py-2 text-[11px] text-muted-foreground">
			<span className="flex items-center gap-1.5">
				<Box size={14} className="opacity-50" />
				{runtime ? `runtime ${runtime}` : "runtime —"}
			</span>
			{webui ? (
				<span className="pl-[22px]">portal {webui}</span>
			) : (
				<span className="pl-[22px] opacity-70">portal — (headless)</span>
			)}
		</div>
	);
}
