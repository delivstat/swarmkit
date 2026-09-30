import { formatBriefDate } from "@/lib/format";
import type { HistoryEntry } from "@/lib/types";

// Prior 7 briefs (design/details/dot-app.md §3.2). Horizontal chip strip; hidden below ~360 px
// widths would be a nice-to-have but not needed — the strip scrolls horizontally on mobile.
export function HistoryStrip({ entries }: { entries: HistoryEntry[] }) {
	if (entries.length === 0) return null;
	return (
		<section aria-labelledby="history-heading" className="mt-8">
			<h3
				id="history-heading"
				className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
			>
				Previous 7 days
			</h3>
			<ul className="mt-2 flex gap-2 overflow-x-auto pb-2">
				{entries.map((e) => (
					<li key={e.runId}>
						<button
							type="button"
							disabled
							className="whitespace-nowrap rounded-md border border-border bg-card px-3 py-2 text-xs text-muted-foreground hover:bg-accent disabled:cursor-not-allowed disabled:opacity-70"
						>
							<span className="block font-medium text-foreground">
								{formatBriefDate(e.generatedAt)}
							</span>
							<span>{e.itemCount} items</span>
						</button>
					</li>
				))}
			</ul>
		</section>
	);
}
