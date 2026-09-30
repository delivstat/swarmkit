import { HistoryStrip } from "@/components/history-strip";
import { ItemCard } from "@/components/item-card";
import { orderedItems, staticBrief, staticHistory } from "@/lib/fixtures";
import { formatBriefDate } from "@/lib/format";

// Static render (design/details/dot-app.md §Commit split step 3). Commit 4 swaps the fixture
// for a real fetch against POST /api/mcp/{server_id}/invoke via the runtime proxy.
export default function TodaysBrief() {
	const items = orderedItems(staticBrief);
	return (
		<main className="mx-auto max-w-screen-md px-4 py-6 md:py-10">
			<header className="flex items-center justify-between gap-3">
				<div>
					<p className="text-xs uppercase tracking-wide text-muted-foreground">
						DOT · today's brief
					</p>
					<h1 className="text-xl font-semibold tracking-tight md:text-2xl">
						{formatBriefDate(staticBrief.generatedAt)}
					</h1>
				</div>
				<button
					type="button"
					className="inline-flex min-h-11 items-center rounded-md border border-input bg-background px-3 text-sm font-medium hover:bg-accent"
					disabled
					aria-label="Refresh brief"
				>
					Refresh
				</button>
			</header>
			<section aria-label="Ranked items" className="mt-6 space-y-3">
				{items.map((item) => (
					<ItemCard key={item.id} item={item} />
				))}
			</section>
			<HistoryStrip entries={staticHistory} />
			<p className="mt-8 text-xs text-muted-foreground">
				Static fixture. Live wiring lands in commit 4.
			</p>
		</main>
	);
}
