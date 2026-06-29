import { Badge, type BadgeProps } from "@/components/ui/badge";
import type { CommandStatus, ConnectionMode, Health } from "@/lib/types";

const HEALTH_VARIANT: Record<Health, BadgeProps["variant"]> = {
	healthy: "success",
	stale: "warning",
	unreachable: "destructive",
	unknown: "muted",
};

export function HealthBadge({ health }: { health: Health }) {
	return <Badge variant={HEALTH_VARIANT[health]}>{health}</Badge>;
}

export function ConnectionBadge({
	connection,
}: { connection: ConnectionMode }) {
	return (
		<Badge variant="outline">
			{connection === "poll" ? "poll (Mode B)" : "direct (Mode A)"}
		</Badge>
	);
}

const COMMAND_VARIANT: Record<CommandStatus, BadgeProps["variant"]> = {
	queued: "muted",
	dispatched: "warning",
	done: "success",
	error: "destructive",
};

export function CommandStatusBadge({ status }: { status: CommandStatus }) {
	return <Badge variant={COMMAND_VARIANT[status]}>{status}</Badge>;
}
