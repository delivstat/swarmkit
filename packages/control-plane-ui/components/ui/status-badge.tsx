import { Badge, type BadgeProps } from "@/components/ui/badge";

/**
 * Generic status → badge-variant mapping for the fleet's many status strings (proposals, jobs,
 * drift, eval outcomes). Health/command badges have their own typed maps; this covers the rest with
 * one convention so a "pending"/"ok"/"error" reads the same colour everywhere. Unknown values fall
 * back to the neutral `muted` variant.
 */
const STATUS_VARIANT: Record<string, BadgeProps["variant"]> = {
	// good
	ok: "success",
	healthy: "success",
	approved: "success",
	done: "success",
	completed: "success",
	pass: "success",
	deployed: "success",
	// in-progress / neutral-warn
	pending: "warning",
	queued: "warning",
	dispatched: "warning",
	running: "warning",
	drift: "warning",
	stale: "warning",
	// bad
	error: "destructive",
	failed: "destructive",
	rejected: "destructive",
	unreachable: "destructive",
	missing: "destructive",
	fail: "destructive",
};

export function StatusBadge({
	status,
	className,
}: {
	status: string;
	className?: string;
}) {
	const variant = STATUS_VARIANT[status.toLowerCase()] ?? "muted";
	return (
		<Badge variant={variant} className={className}>
			{status}
		</Badge>
	);
}
