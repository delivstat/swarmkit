import { cn } from "@/lib/utils";

/**
 * The four-state wrapper every list/detail on the dashboard needs: first-load skeleton, error,
 * empty, and data. Pages used to hand-roll `if (loading) … if (error) … if (!data.length) …`
 * inconsistently; `<ResourceView>` gives one accessible, uniform rendering. Feed it the flags from a
 * `useResource` result. `error` shows without hiding stale data only if `data` is also empty —
 * otherwise the caller keeps rendering the last-known data with the error surfaced elsewhere.
 * (Named `ResourceView`, not `DataView`, to avoid shadowing the JS built-in `DataView`.)
 */
export function ResourceView({
	loading,
	error,
	isEmpty,
	emptyLabel = "Nothing here yet.",
	children,
	className,
}: {
	loading: boolean;
	error?: string | null;
	isEmpty?: boolean;
	emptyLabel?: string;
	children: React.ReactNode;
	className?: string;
}) {
	if (loading) {
		return (
			<output
				aria-busy="true"
				className={cn(
					"block animate-pulse text-sm text-muted-foreground",
					className,
				)}
			>
				Loading…
			</output>
		);
	}
	if (error) {
		return (
			<div role="alert" className={cn("text-sm text-destructive", className)}>
				{error}
			</div>
		);
	}
	if (isEmpty) {
		return (
			<p className={cn("text-sm text-muted-foreground", className)}>
				{emptyLabel}
			</p>
		);
	}
	return <>{children}</>;
}

/**
 * A keyboard-operable row: `Enter`/`Space` activate it like a click, and it's focusable and exposed
 * as a button to assistive tech. Use for clickable table/list rows (drill into an instance,
 * artifact, proposal) so the fleet panel is navigable without a mouse.
 */
export function SelectableRow({
	onSelect,
	children,
	className,
	as: Tag = "tr",
}: {
	onSelect: () => void;
	children: React.ReactNode;
	className?: string;
	as?: "tr" | "div" | "li";
}) {
	return (
		// biome-ignore lint/a11y/useSemanticElements: a <tr> can't be a real <button> — role=button + key handling is the accessible pattern for a clickable row.
		<Tag
			role="button"
			tabIndex={0}
			onClick={onSelect}
			onKeyDown={(e: React.KeyboardEvent) => {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					onSelect();
				}
			}}
			className={cn(
				"cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring",
				className,
			)}
		>
			{children}
		</Tag>
	);
}
