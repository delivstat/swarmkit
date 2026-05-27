import { cn } from "@/lib/cn";

const COLORS: Record<string, string> = {
	completed: "var(--success)",
	running: "var(--accent)",
	pending: "var(--warning)",
	failed: "var(--error)",
};

export function StatusBadge({ status }: { status: string }) {
	const color = COLORS[status] ?? "var(--fg-muted)";
	return (
		<span
			className={cn(
				"inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full",
			)}
			style={{ color, border: `1px solid ${color}30` }}
		>
			<span
				className="w-1.5 h-1.5 rounded-full"
				style={{ background: color }}
			/>
			{status}
		</span>
	);
}
