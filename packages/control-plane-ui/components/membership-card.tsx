"use client";

import { LogOut, Users } from "lucide-react";
import { useCallback, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Membership } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

/**
 * The fleet relationship **this** panel holds for the instance (design 20): fleet id, scope,
 * fingerprint, created — read from the panel's encrypted credential store. Multi-fleet visibility is
 * panel-perspective: a fleet sees only its own membership. "Leave fleet" self-revokes the membership
 * on the instance with the membership key itself, then forgets the stored credential.
 */
export function MembershipCard({ instanceId }: { instanceId: string }) {
	const fetcher = useCallback(() => api.membership(instanceId), [instanceId]);
	const { data, error, loading, refresh } = useResource<Membership>(
		`/instances/${instanceId}/membership`,
		fetcher,
		{ refreshInterval: 0 },
	);
	const [leaving, setLeaving] = useState(false);
	const [leaveError, setLeaveError] = useState<string | null>(null);

	async function leave() {
		if (
			!confirm(
				"Leave this fleet? The membership is revoked on the instance and the stored credential is forgotten.",
			)
		)
			return;
		setLeaving(true);
		setLeaveError(null);
		try {
			await api.leaveFleet(instanceId);
			refresh();
		} catch (err) {
			setLeaveError(err instanceof Error ? err.message : String(err));
		} finally {
			setLeaving(false);
		}
	}

	// GET /membership 404s when this fleet holds no membership — nothing to show.
	const none = !data && !loading;

	return (
		<Card>
			<CardHeader className="flex-row items-start justify-between space-y-0">
				<CardTitle className="flex items-center gap-2">
					<Users className="size-4" />
					Fleet membership
				</CardTitle>
				{data ? (
					<Button
						variant="outline"
						size="sm"
						onClick={leave}
						disabled={leaving}
					>
						<LogOut />
						{leaving ? "Leaving…" : "Leave fleet"}
					</Button>
				) : null}
			</CardHeader>
			<CardContent className="space-y-2 text-sm">
				{leaveError ? <p className="text-destructive">{leaveError}</p> : null}
				{none ? (
					<p className="text-muted-foreground">
						This fleet holds no membership for the instance. Enrol it above to
						establish one.
					</p>
				) : null}
				{data ? (
					<div className="space-y-1">
						<div className="flex items-center gap-2">
							<span className="text-muted-foreground">Fleet</span>
							<code className="rounded bg-muted px-1 py-0.5 text-xs">
								{data.fleet_id}
							</code>
							<Badge variant="muted">{data.scope}</Badge>
						</div>
						<div className="text-xs text-muted-foreground">
							membership {data.membership_id} · fingerprint {data.fingerprint} ·
							since {new Date(data.created_at).toLocaleString()}
						</div>
					</div>
				) : null}
				{error && !data && !none ? (
					<p className="text-destructive">{error}</p>
				) : null}
			</CardContent>
		</Card>
	);
}
