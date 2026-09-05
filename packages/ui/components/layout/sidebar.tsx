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
	PanelLeftClose,
	PanelLeftOpen,
	PenTool,
	Plug,
	Puzzle,
	RefreshCw,
	ScrollText,
	Timer,
	Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import {
	SIDEBAR_WIDTH,
	isActive,
	loadCollapsed,
	storeCollapsed,
} from "@/lib/sidebar-state";
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
	{ href: "/contracts", label: "Contracts", icon: Handshake },
	{ href: "/comprehension", label: "Comprehension", icon: Gauge },
	{ href: "/memory", label: "Memory", icon: BrainCircuit },
	{ href: "/canary", label: "Canary", icon: ListChecks },
	{ href: "/triggers", label: "Triggers", icon: Timer },
	{ href: "/connections", label: "Connections", icon: Plug },
	{ href: "/system", label: "System", icon: Database },
] as const;

/**
 * The nav, which scrolls, and collapses to icons.
 *
 * Eighteen entries sat in a `flex-col` with no overflow handling, inside a body that is
 * `h-screen overflow-hidden`. On a short viewport — a laptop with devtools open, a split screen —
 * the entries past the fold were not merely awkward to reach, they were unreachable: nothing
 * scrolled, and the footer's `mt-auto` pushed the list further up. System, Triggers and Canary
 * were the first to go.
 *
 * So: the link list is its own scroll region between a fixed header and a fixed footer, and the
 * whole rail collapses to icon width for anyone who would rather have the horizontal space.
 */
export function Sidebar() {
	const pathname = usePathname();
	// Always expanded on the first render. The server has no `window`, so reading the stored
	// preference here would produce markup the client disagrees with and make React throw the tree
	// away. The effect below corrects it before paint.
	const [collapsed, setCollapsed] = useState(false);

	useEffect(() => {
		const stored = loadCollapsed();
		if (stored !== null) setCollapsed(stored);
	}, []);

	const toggle = () => {
		setCollapsed((prev) => {
			storeCollapsed(!prev);
			return !prev;
		});
	};

	return (
		<nav
			className={cn(
				"flex shrink-0 flex-col border-r bg-card transition-[width] duration-150",
				collapsed ? SIDEBAR_WIDTH.collapsed : SIDEBAR_WIDTH.expanded,
			)}
			aria-label="Main"
		>
			{/* Fixed header. Shrink-0 throughout, or the flex parent compresses these instead of
			    scrolling the list — which is what "no overflow handling" looked like. */}
			<div className="flex shrink-0 items-center gap-2 border-b p-3">
				{!collapsed && (
					<div className="min-w-0 flex-1 px-1">
						<h1 className="truncate text-lg font-semibold tracking-tight">
							SwarmKit
						</h1>
						<p className="truncate text-xs text-muted-foreground">
							Runtime Dashboard
						</p>
					</div>
				)}
				<button
					type="button"
					onClick={toggle}
					aria-expanded={!collapsed}
					aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
					title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
					className={cn(
						"rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground",
						collapsed && "mx-auto",
					)}
				>
					{collapsed ? (
						<PanelLeftOpen size={16} />
					) : (
						<PanelLeftClose size={16} />
					)}
				</button>
			</div>

			{/* The scroll region. `min-h-0` is what actually makes it scroll: a flex child's default
			    min-height is its content, so without it the list grows past the container and the
			    overflow never engages. */}
			<div className="min-h-0 flex-1 overflow-y-auto p-3">
				<ul className="flex flex-col gap-1">
					{NAV.map(({ href, label, icon: Icon }) => {
						const active = isActive(pathname, href);
						return (
							<li key={href}>
								<Link
									href={href}
									// The label is the accessible name when it is visible; when it is
									// not, `title` carries it for pointer users and `aria-label` for
									// assistive tech. A collapsed rail of unlabelled icons is not a
									// navigation anyone can use.
									title={collapsed ? label : undefined}
									aria-label={collapsed ? label : undefined}
									aria-current={active ? "page" : undefined}
									className={cn(
										"flex items-center rounded-md py-2 text-sm transition-colors",
										collapsed ? "justify-center px-2" : "gap-2.5 px-3",
										active
											? "bg-accent font-medium text-accent-foreground"
											: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
									)}
								>
									<Icon size={16} className="shrink-0" />
									{!collapsed && <span className="truncate">{label}</span>}
								</Link>
							</li>
						);
					})}
				</ul>
			</div>

			<ReloadWorkspace collapsed={collapsed} />
			<VersionFooter collapsed={collapsed} />
		</nav>
	);
}

/** Re-read the workspace from disk.
 *
 * `swarmkit serve` resolves the workspace once at startup and holds it. Edits made through the UI
 * already trigger a reload, but a topology, skill or archetype changed on disk — by an editor, a
 * git pull, an authoring swarm writing files — was invisible until the server was restarted, with
 * nothing to say the running config and the files had diverged.
 *
 * The outcome is reported rather than assumed. A reload that fails validation leaves the PREVIOUS
 * runtime serving, so "invalid" here means the change on disk is not live — not that a broken
 * config is now running. Those are opposite situations and the wrong reading sends an operator
 * looking for the wrong problem.
 */
function ReloadWorkspace({ collapsed }: { collapsed: boolean }) {
	const [busy, setBusy] = useState(false);
	const [result, setResult] = useState<{ ok: boolean; text: string } | null>(
		null,
	);

	const reload = async () => {
		setBusy(true);
		setResult(null);
		try {
			const validation = await api.reloadWorkspace();
			setResult(
				validation.valid
					? {
							ok: true,
							text: `Reloaded — ${validation.topologies.length} topologies, ${validation.skills.length} skills`,
						}
					: {
							ok: false,
							text: "Workspace is invalid — the previous config is still serving",
						},
			);
		} catch (err) {
			setResult({
				ok: false,
				text: err instanceof Error ? err.message : "Reload failed",
			});
		} finally {
			setBusy(false);
		}
	};

	return (
		<div className="shrink-0 border-t p-3">
			<button
				type="button"
				onClick={reload}
				disabled={busy}
				title="Re-read topologies, skills and archetypes from disk"
				aria-label="Reload workspace from disk"
				className={cn(
					"flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground disabled:opacity-50",
					collapsed && "justify-center",
				)}
			>
				<RefreshCw
					size={16}
					className={cn("shrink-0", busy && "animate-spin")}
				/>
				{!collapsed && <span>{busy ? "Reloading…" : "Reload workspace"}</span>}
			</button>
			{!collapsed && result && (
				<p
					className={cn(
						"mt-1 px-2 text-[11px]",
						result.ok ? "text-muted-foreground" : "text-destructive",
					)}
				>
					{result.text}
				</p>
			)}
		</div>
	);
}

/** Both component versions, read from the server.
 *
 * This footer used to be a hardcoded `v1.2.58` — a literal for neither package, left behind when
 * the runtime was at 1.2.x. It also showed ONE number, which cannot be right: `swarmkit serve` is
 * the runtime hosting a separately versioned portal, and a mismatch between them is exactly what a
 * reader needs to see (an old portal paired with a new runtime is a real and silent failure mode).
 *
 * It no longer uses `mt-auto` — the scroll region above takes the slack now, and `mt-auto` on a
 * list that overflows pushes the links off-screen rather than pinning the footer down.
 */
function VersionFooter({ collapsed }: { collapsed: boolean }) {
	const [health, setHealth] = useState<HealthResponse | null>(null);

	useEffect(() => {
		api
			.health()
			.then(setHealth)
			.catch(() => setHealth(null));
	}, []);

	const runtime = health?.runtime_version;
	const webui = health?.webui_version;

	if (collapsed) {
		// Both numbers still reachable, since a version mismatch is the thing worth noticing.
		return (
			<div
				className="shrink-0 border-t px-3 py-2 text-center text-muted-foreground"
				title={`runtime ${runtime ?? "—"} · portal ${webui ?? "— (headless)"}`}
			>
				<Box size={14} className="mx-auto opacity-50" />
			</div>
		);
	}

	return (
		<div className="flex shrink-0 flex-col gap-0.5 border-t px-3 py-2 text-[11px] text-muted-foreground">
			<span className="flex items-center gap-1.5">
				<Box size={14} className="shrink-0 opacity-50" />
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
