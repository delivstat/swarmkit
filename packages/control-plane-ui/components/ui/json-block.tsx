import { cn } from "@/lib/utils";

/**
 * Pretty-printed, scrollable JSON. One place for rendering artifact content, command output,
 * eval summaries, etc. — instead of ad-hoc `<pre>{JSON.stringify(x, null, 2)}</pre>` scattered
 * across pages. Strings are shown verbatim (already human-readable); everything else is formatted.
 */
export function JsonBlock({
	value,
	className,
}: {
	value: unknown;
	className?: string;
}) {
	const text =
		typeof value === "string" ? value : JSON.stringify(value, null, 2);
	return (
		<pre
			className={cn(
				"max-h-96 overflow-auto rounded-md bg-muted p-3 font-mono text-xs text-muted-foreground",
				className,
			)}
		>
			{text}
		</pre>
	);
}
