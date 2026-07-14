import { cn } from "@/lib/utils";

// status → (text/dot color, animate the dot?). A colored dot + label pill — kept from the original
// design, now on semantic tokens. Unknown statuses fall back to muted.
const STYLES: Record<string, { color: string; pulse?: boolean }> = {
	completed: { color: "text-success" },
	succeeded: { color: "text-success" },
	running: { color: "text-sky-500", pulse: true },
	pending: { color: "text-warning" },
	queued: { color: "text-warning" },
	failed: { color: "text-destructive" },
	error: { color: "text-destructive" },
};

export function StatusBadge({ status }: { status: string }) {
	const style = STYLES[status] ?? { color: "text-muted-foreground" };
	return (
		<span
			className={cn(
				"inline-flex items-center gap-1.5 rounded-full border border-current/20 px-2 py-0.5 text-xs font-medium",
				style.color,
			)}
		>
			<span
				className={cn(
					"size-1.5 rounded-full bg-current",
					style.pulse && "animate-pulse",
				)}
			/>
			{status}
		</span>
	);
}
