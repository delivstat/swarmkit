import type { Source } from "@/lib/types";

const LABEL: Record<Source, string> = {
	gmail: "Gmail",
	calendar: "Calendar",
};

// Two-tone chip so the source reads at a glance (design/details/dot-app.md §3.2 Today's brief).
export function SourceBadge({ source }: { source: Source }) {
	return (
		<span className="inline-flex items-center gap-1 rounded-md border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
			<span
				aria-hidden
				className="inline-block h-1.5 w-1.5 rounded-full bg-foreground/60"
			/>
			{LABEL[source]}
		</span>
	);
}
