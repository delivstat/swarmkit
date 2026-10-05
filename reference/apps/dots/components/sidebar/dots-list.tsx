"use client";

import type { Dot } from "@/lib/dots.config";
import { cn } from "@/lib/utils";
import { ChartNoAxesColumn, GitFork, Plug, Plus, Reply, Sparkles, Sunrise } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const ICONS: Record<string, React.ElementType> = {
	sunrise: Sunrise,
	"mail-reply": Reply,
	github: GitFork,
	sparkles: Sparkles,
};

const UTILITY_LINKS: Array<{ href: string; label: string; icon: React.ElementType }> = [
	{ href: "/connections", label: "Connections", icon: Plug },
	{ href: "/usage", label: "Usage & cost", icon: ChartNoAxesColumn },
];

export function DotsList({ dots }: { dots: Dot[] }): React.ReactElement {
	const pathname = usePathname();
	return (
		<nav className="flex h-full flex-col gap-1 p-3">
			<div className="flex items-center justify-between px-2 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
				<span>Dots</span>
				<Link
					href="/dots/author"
					aria-label="Add a coworker"
					className={cn(
						"rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground",
						pathname === "/dots/author" && "bg-accent text-accent-foreground",
					)}
				>
					<Plus className="h-4 w-4" aria-hidden="true" />
				</Link>
			</div>
			{dots.map((dot) => {
				const Icon = ICONS[dot.icon] ?? Sunrise;
				const href = `/dots/${dot.id}`;
				const active = pathname === href;
				return (
					<Link
						key={dot.id}
						href={href}
						className={cn(
							"flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
							active
								? "bg-accent text-accent-foreground"
								: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
						)}
					>
						<Icon className="h-4 w-4" aria-hidden="true" />
						<span>{dot.name}</span>
					</Link>
				);
			})}
			<div className="mt-auto space-y-1 border-t border-border pt-3">
				{UTILITY_LINKS.map(({ href, label, icon: Icon }) => {
					const active = pathname === href;
					return (
						<Link
							key={href}
							href={href}
							className={cn(
								"flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
								active
									? "bg-accent text-accent-foreground"
									: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
							)}
						>
							<Icon className="h-4 w-4" aria-hidden="true" />
							<span>{label}</span>
						</Link>
					);
				})}
			</div>
		</nav>
	);
}
