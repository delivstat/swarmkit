"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const LINKS = [
	{ href: "/", label: "Brief" },
	{ href: "/connections", label: "Connections" },
	{ href: "/settings", label: "Settings" },
	{ href: "/activity", label: "Activity" },
	{ href: "/usage", label: "Usage" },
] as const;

// Persistent top nav. Rendered in app/layout.tsx below the <body> start, so every authenticated
// page carries it; the /login route hides it via a path check (same layout serves both).
export function TopNav() {
	const pathname = usePathname();
	const router = useRouter();
	if (pathname === "/login") return null;

	async function signOut() {
		await fetch("/api/auth/logout", { method: "POST" });
		router.replace("/login");
		router.refresh();
	}

	return (
		<header className="sticky top-0 z-10 border-b border-border bg-background/90 backdrop-blur">
			<div className="mx-auto flex max-w-screen-lg items-center justify-between gap-3 px-4 py-2">
				<Link
					href="/"
					className="text-sm font-semibold tracking-tight"
					aria-label="DOT home"
				>
					DOT
				</Link>
				<nav
					aria-label="Primary"
					className="flex flex-1 items-center gap-1 overflow-x-auto"
				>
					{LINKS.map((l) => {
						const active =
							l.href === "/" ? pathname === "/" : pathname.startsWith(l.href);
						return (
							<Link
								key={l.href}
								href={l.href}
								aria-current={active ? "page" : undefined}
								className={
									active
										? "whitespace-nowrap rounded-md bg-accent px-2 py-1 text-sm font-medium text-foreground"
										: "whitespace-nowrap rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent/60 hover:text-foreground"
								}
							>
								{l.label}
							</Link>
						);
					})}
				</nav>
				<button
					type="button"
					onClick={signOut}
					className="whitespace-nowrap rounded-md border border-input bg-background px-2 py-1 text-sm font-medium hover:bg-accent"
				>
					Sign out
				</button>
			</div>
		</header>
	);
}
