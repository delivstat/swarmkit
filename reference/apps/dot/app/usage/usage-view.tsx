"use client";

import {
	type UsageBreakdown,
	type UsageDaily,
	type UsageSummary,
	type UsageTotals,
	formatTokens,
	formatUsd,
} from "@/lib/usage";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

async function fetchJson<T>(url: string): Promise<T> {
	const res = await fetch(url, { cache: "no-store" });
	if (!res.ok) throw new Error(`HTTP ${res.status}`);
	return (await res.json()) as T;
}

export function UsageView() {
	const summary = useQuery({
		queryKey: ["usage", "summary"],
		queryFn: () => fetchJson<UsageSummary>("/api/usage/summary"),
	});
	const daily = useQuery({
		queryKey: ["usage", "daily", 30],
		queryFn: () => fetchJson<UsageDaily>("/api/usage/daily?window=30"),
	});

	return (
		<main className="mx-auto max-w-screen-md px-4 py-6 md:py-10">
			<Link
				href="/"
				className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
			>
				<span aria-hidden>←</span> Back to brief
			</Link>
			<header className="mt-4">
				<h1 className="text-xl font-semibold tracking-tight md:text-2xl">
					Usage & cost
				</h1>
				<p className="mt-1 text-sm text-muted-foreground">
					Tokens and cost across the DOT topologies. Numbers come straight from
					the audit log.
				</p>
			</header>
			<section className="mt-6 grid gap-3 sm:grid-cols-3">
				<StatTile title="Today" totals={summary.data?.today} />
				<StatTile title="Month to date" totals={summary.data?.monthToDate} />
				<StatTile title="Last 30 days" totals={summary.data?.last30Days} />
			</section>
			<section className="mt-6 rounded-lg border border-border bg-card p-4 shadow-sm">
				<h2 className="text-base font-semibold">Daily cost (last 30 days)</h2>
				{daily.data ? (
					<DailyChart data={daily.data} />
				) : (
					<div className="mt-3 h-32 animate-pulse rounded bg-muted" />
				)}
			</section>
			<Breakdowns />
		</main>
	);
}

function StatTile({ title, totals }: { title: string; totals?: UsageTotals }) {
	return (
		<article className="rounded-lg border border-border bg-card p-4 shadow-sm">
			<h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
				{title}
			</h3>
			{totals ? (
				<>
					<p className="mt-2 text-2xl font-semibold tabular-nums">
						{formatUsd(totals.costUsd)}
					</p>
					<p className="mt-1 text-xs text-muted-foreground">
						{formatTokens(totals.tokensIn)} in ·{" "}
						{formatTokens(totals.tokensOut)} out
					</p>
				</>
			) : (
				<div className="mt-2 h-6 w-20 animate-pulse rounded bg-muted" />
			)}
		</article>
	);
}

function DailyChart({ data }: { data: UsageDaily }) {
	const w = 600;
	const h = 120;
	const pad = { top: 8, right: 4, bottom: 20, left: 32 };
	const innerW = w - pad.left - pad.right;
	const innerH = h - pad.top - pad.bottom;
	const maxCost = Math.max(1, ...data.days.map((d) => d.costUsd));
	const barW = innerW / data.days.length;
	const bars = data.days.map((d, i) => {
		const height = (d.costUsd / maxCost) * innerH;
		return {
			x: pad.left + i * barW,
			y: pad.top + innerH - height,
			w: Math.max(barW - 2, 1),
			h: height,
			d,
		};
	});
	const yTicks = [0, 0.5, 1].map((f) => ({
		y: pad.top + innerH - f * innerH,
		label: formatUsd(maxCost * f),
	}));
	return (
		<svg
			role="img"
			aria-label="Daily cost bar chart"
			viewBox={`0 0 ${w} ${h}`}
			className="mt-3 h-32 w-full"
		>
			{yTicks.map((t) => (
				<g key={t.y}>
					<line
						x1={pad.left}
						x2={w - pad.right}
						y1={t.y}
						y2={t.y}
						stroke="currentColor"
						className="text-border"
						strokeDasharray="2 3"
					/>
					<text
						x={pad.left - 4}
						y={t.y + 3}
						textAnchor="end"
						className="fill-muted-foreground text-[9px]"
					>
						{t.label}
					</text>
				</g>
			))}
			{bars.map((b) => (
				<rect
					key={b.d.date}
					x={b.x}
					y={b.y}
					width={b.w}
					height={b.h}
					className="fill-sky-500"
				>
					<title>
						{b.d.date} · {formatUsd(b.d.costUsd)} · {formatTokens(b.d.tokensIn)}{" "}
						in / {formatTokens(b.d.tokensOut)} out
					</title>
				</rect>
			))}
			<text x={pad.left} y={h - 4} className="fill-muted-foreground text-[9px]">
				{data.days[0]?.date}
			</text>
			<text
				x={w - pad.right}
				y={h - 4}
				textAnchor="end"
				className="fill-muted-foreground text-[9px]"
			>
				{data.days[data.days.length - 1]?.date}
			</text>
		</svg>
	);
}

function Breakdowns() {
	const [by, setBy] = useState<"provider" | "topology">("provider");
	const { data, isLoading } = useQuery({
		queryKey: ["usage", "breakdown", by, "month"],
		queryFn: () =>
			fetchJson<UsageBreakdown>(`/api/usage/breakdown?by=${by}&window=month`),
	});
	const max = Math.max(1, ...(data?.rows.map((r) => r.costUsd) ?? [1]));
	return (
		<section className="mt-6 rounded-lg border border-border bg-card p-4 shadow-sm">
			<div className="flex items-center justify-between gap-2">
				<h2 className="text-base font-semibold">Breakdown</h2>
				<div className="inline-flex rounded-md border border-input bg-background p-0.5 text-xs">
					{(["provider", "topology"] as const).map((v) => (
						<button
							key={v}
							type="button"
							onClick={() => setBy(v)}
							className={
								v === by
									? "rounded-sm bg-primary px-2 py-1 font-medium text-primary-foreground"
									: "rounded-sm px-2 py-1 text-muted-foreground hover:text-foreground"
							}
						>
							{v}
						</button>
					))}
				</div>
			</div>
			{isLoading && !data && (
				<div className="mt-3 h-24 animate-pulse rounded bg-muted" />
			)}
			{data && data.rows.length === 0 && (
				<p className="mt-3 text-sm text-muted-foreground">
					No usage yet in this window.
				</p>
			)}
			<ol className="mt-3 space-y-2">
				{data?.rows.map((r) => (
					<li key={r.label}>
						<div className="flex items-baseline justify-between gap-2 text-sm">
							<span className="truncate font-medium">{r.label}</span>
							<span className="tabular-nums text-muted-foreground">
								{formatUsd(r.costUsd)} ·{" "}
								{formatTokens(r.tokensIn + r.tokensOut)} tok
							</span>
						</div>
						<div className="mt-1 h-1.5 overflow-hidden rounded bg-muted">
							<div
								className="h-full bg-sky-500"
								style={{ width: `${(r.costUsd / max) * 100}%` }}
							/>
						</div>
					</li>
				))}
			</ol>
		</section>
	);
}
