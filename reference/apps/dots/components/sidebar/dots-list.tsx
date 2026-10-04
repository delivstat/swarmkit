"use client";

import { DOTS } from "@/lib/dots.config";
import { cn } from "@/lib/utils";
import { GitFork, Reply, Sunrise } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const ICONS: Record<string, React.ElementType> = {
	sunrise: Sunrise,
	"mail-reply": Reply,
	github: GitFork,
};

export function DotsList(): React.ReactElement {
	const pathname = usePathname();
	return (
		<nav className="flex h-full flex-col gap-1 p-3">
			<div className="px-2 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
				Dots
			</div>
			{DOTS.map((dot) => {
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
		</nav>
	);
}
