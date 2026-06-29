"use client";

import {
	BadgeCheck,
	BarChart3,
	BotMessageSquare,
	LayoutDashboard,
	type LucideIcon,
	Package,
	PlayCircle,
	Server,
	Settings,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { SignOut } from "@/components/auth-gate";
import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";

interface NavItem {
	href: string;
	label: string;
	icon: LucideIcon;
	planned?: boolean;
}

// Page set from design/details/control-plane/16-fleet-ui.md. Live routes link;
// planned routes render muted until their slice lands.
const NAV: NavItem[] = [
	{ href: "/dashboard", label: "Fleet", icon: LayoutDashboard },
	{ href: "/instances", label: "Instances", icon: Server },
	{ href: "/runs", label: "Runs", icon: PlayCircle, planned: true },
	{ href: "/evals", label: "Evals", icon: BarChart3, planned: true },
	{ href: "/artifacts", label: "Artifacts", icon: Package, planned: true },
	{ href: "/approvals", label: "Approvals", icon: BadgeCheck, planned: true },
	{
		href: "/authoring",
		label: "Authoring",
		icon: BotMessageSquare,
		planned: true,
	},
	{ href: "/settings", label: "Settings", icon: Settings, planned: true },
];

export function AppSidebar() {
	const pathname = usePathname();

	return (
		<aside className="flex h-screen w-60 shrink-0 flex-col border-r bg-card">
			<div className="flex h-14 items-center justify-between border-b px-4">
				<Link
					href="/dashboard"
					className="flex items-center gap-2 font-semibold"
				>
					<span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
						<Server className="size-4" />
					</span>
					SwarmKit
				</Link>
				<ThemeToggle />
			</div>

			<nav className="flex-1 space-y-1 overflow-y-auto p-3">
				{NAV.map((item) => {
					const active =
						pathname === item.href || pathname.startsWith(`${item.href}/`);
					if (item.planned) {
						return (
							<div
								key={item.href}
								className="flex cursor-not-allowed items-center justify-between gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground/50"
							>
								<span className="flex items-center gap-3">
									<item.icon className="size-4" />
									{item.label}
								</span>
								<span className="text-[10px] uppercase tracking-wide">
									soon
								</span>
							</div>
						);
					}
					return (
						<Link
							key={item.href}
							href={item.href}
							className={cn(
								"flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
								active
									? "bg-accent text-accent-foreground"
									: "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
							)}
						>
							<item.icon className="size-4" />
							{item.label}
						</Link>
					);
				})}
			</nav>

			<SignOut />
			<div className="border-t p-3 text-xs text-muted-foreground">
				Fleet control plane
			</div>
		</aside>
	);
}
