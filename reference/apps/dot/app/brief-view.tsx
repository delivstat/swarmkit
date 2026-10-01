"use client";

import { HistoryStrip } from "@/components/history-strip";
import { ItemCard } from "@/components/item-card";
import { staticHistory } from "@/lib/fixtures";
import { formatBriefDate } from "@/lib/format";
import type { Brief } from "@/lib/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// Live client wired via React Query. staticHistory stays until the history endpoint lands in a
// later commit — it renders below the fold and does not gate the primary path.
export function BriefView({ initial }: { initial: Brief | null }) {
	const qc = useQueryClient();
	const { data, error, isFetching } = useQuery<Brief, BriefError>({
		queryKey: ["brief"],
		queryFn: fetchBrief,
		initialData: initial ?? undefined,
		refetchInterval: 30_000,
	});

	const rerun = useMutation({
		mutationFn: rerunBrief,
		onSuccess: (fresh) => qc.setQueryData(["brief"], fresh),
	});

	if (error && !data) {
		return <ErrorBox message={error.detail ?? error.message} />;
	}
	const brief = data;
	if (!brief) return <SkeletonBrief />;

	return (
		<main className="mx-auto max-w-screen-md px-4 py-6 md:py-10">
			<header className="flex items-center justify-between gap-3">
				<div>
					<p className="text-xs uppercase tracking-wide text-muted-foreground">
						DOT · today's brief
					</p>
					<h1 className="text-xl font-semibold tracking-tight md:text-2xl">
						{formatBriefDate(brief.generatedAt)}
					</h1>
				</div>
				<button
					type="button"
					onClick={() => rerun.mutate()}
					disabled={rerun.isPending || isFetching}
					className="inline-flex min-h-11 items-center rounded-md border border-input bg-background px-3 text-sm font-medium hover:bg-accent disabled:opacity-60"
					aria-label="Refresh brief"
				>
					{rerun.isPending || isFetching ? "Refreshing…" : "Refresh"}
				</button>
			</header>
			{brief.items.length === 0 ? (
				<EmptyBrief
					onRefresh={() => rerun.mutate()}
					pending={rerun.isPending}
				/>
			) : (
				<section aria-label="Ranked items" className="mt-6 space-y-3">
					{brief.items.map((item) => (
						<ItemCard key={item.id} item={item} />
					))}
				</section>
			)}
			<HistoryStrip entries={staticHistory} />
		</main>
	);
}

interface BriefError extends Error {
	detail?: string;
}

async function fetchBrief(): Promise<Brief> {
	const res = await fetch("/api/brief", { cache: "no-store" });
	if (!res.ok) throw await toError(res);
	return (await res.json()) as Brief;
}

async function rerunBrief(): Promise<Brief> {
	const res = await fetch("/api/brief/run", {
		method: "POST",
		cache: "no-store",
	});
	if (!res.ok) throw await toError(res);
	return (await res.json()) as Brief;
}

async function toError(res: Response): Promise<BriefError> {
	const body = (await res.json().catch(() => ({}))) as {
		error?: string;
		detail?: string;
	};
	const e: BriefError = Object.assign(
		new Error(body.error ?? `HTTP ${res.status}`),
		{ detail: body.detail },
	);
	return e;
}

function SkeletonBrief() {
	return (
		<main className="mx-auto max-w-screen-md px-4 py-6 md:py-10">
			<div className="h-6 w-32 animate-pulse rounded bg-muted" />
			<div className="mt-6 space-y-3">
				{[0, 1, 2].map((i) => (
					<div key={i} className="h-28 animate-pulse rounded-lg bg-muted" />
				))}
			</div>
		</main>
	);
}

function EmptyBrief({
	onRefresh,
	pending,
}: { onRefresh: () => void; pending: boolean }) {
	return (
		<section className="mt-8 rounded-lg border border-dashed border-border p-6 text-center">
			<p className="text-sm text-muted-foreground">
				No brief yet today. It runs on the workspace schedule — trigger it now?
			</p>
			<button
				type="button"
				onClick={onRefresh}
				disabled={pending}
				className="mt-3 inline-flex min-h-11 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow disabled:opacity-60"
			>
				{pending ? "Running…" : "Run now"}
			</button>
		</section>
	);
}

function ErrorBox({ message }: { message: string }) {
	return (
		<main className="mx-auto max-w-screen-md px-4 py-6 md:py-10">
			<div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
				<p className="font-medium">Couldn't load the brief.</p>
				<p className="mt-1 text-destructive/80">{message}</p>
			</div>
		</main>
	);
}
