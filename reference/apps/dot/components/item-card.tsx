import { formatTimeShort, relativeShort } from "@/lib/format";
import type { Item } from "@/lib/types";
import Link from "next/link";
import { ActionButtons } from "./action-buttons";
import { SourceBadge } from "./source-badge";

// The card that appears in the brief stack (design/details/dot-app.md §3.2).
// Truncation: 2 lines on mobile, 3 on desktop. Tapping the body — not a button — routes to
// /item/[id] (the transitional detail state; commit 5 fills the destination).
export function ItemCard({ item }: { item: Item }) {
	const timing = item.scheduledFor
		? `${formatTimeShort(item.scheduledFor)} · ${relativeShort(item.scheduledFor)}`
		: item.receivedAt
			? relativeShort(item.receivedAt)
			: null;
	return (
		<article className="rounded-lg border border-border bg-card p-4 shadow-sm">
			<Link
				href={`/item/${item.id}`}
				className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
			>
				<div className="flex items-center justify-between gap-2">
					<SourceBadge source={item.source} />
					{timing && (
						<span className="text-xs text-muted-foreground">{timing}</span>
					)}
				</div>
				<h2 className="mt-2 line-clamp-2 text-base font-semibold leading-snug md:line-clamp-3 md:text-lg">
					{item.title}
				</h2>
				<p className="mt-1 text-sm italic text-muted-foreground">{item.why}</p>
			</Link>
			<div className="mt-3">
				<ActionButtons
					suggested={item.suggestedAction}
					peers={item.peerActions}
				/>
			</div>
		</article>
	);
}
