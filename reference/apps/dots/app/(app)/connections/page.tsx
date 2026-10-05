import { headers } from "next/headers";
import Link from "next/link";

// Server component — fetches through our proxy so the page works whether we're running against
// the mock runtime (empty list) or a live swarmkit serve. The pattern matches reference/apps/dot
// but we skip the react-query layer: a single server-side fetch is enough for a reference app.

interface Row {
	credential_id: string;
	display_name?: string;
	connected?: boolean | null;
	used_by?: string[];
}

async function loadConnections(): Promise<Row[]> {
	const h = await headers();
	const host = h.get("host") ?? "127.0.0.1:3500";
	const protocol = h.get("x-forwarded-proto") ?? "http";
	try {
		const res = await fetch(`${protocol}://${host}/api/connections`, {
			cache: "no-store",
		});
		if (!res.ok) return [];
		const body = (await res.json()) as { rows?: Row[] };
		return body.rows ?? [];
	} catch {
		return [];
	}
}

export default async function ConnectionsPage(): Promise<React.ReactElement> {
	const rows = await loadConnections();
	return (
		<main className="mx-auto max-w-screen-md px-6 py-10">
			<Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
				← Dots
			</Link>
			<h1 className="mt-4 text-2xl font-semibold tracking-tight">Connections</h1>
			<p className="mt-1 text-sm text-muted-foreground">
				OAuth credentials declared by the SwarmKit workspace. Each Dot&apos;s topology can require a
				subset — the <code>Used by</code> count below tells you which skills break if you
				disconnect.
			</p>
			{rows.length === 0 ? (
				<div className="mt-8 rounded-lg border border-border bg-muted/20 p-6 text-sm text-muted-foreground">
					No connections declared, or the runtime isn&apos;t reachable at <code>SWARMKIT_URL</code>.
					The mock runtime does not expose this endpoint; point <code>SWARMKIT_URL</code> at a live{" "}
					<code>swarmkit serve</code> to see your credentials here.
				</div>
			) : (
				<div className="mt-6 space-y-3">
					{rows.map((row) => (
						<div
							key={row.credential_id}
							className="flex items-center justify-between rounded-lg border border-border bg-muted/10 p-4"
						>
							<div>
								<p className="font-medium">{row.display_name ?? row.credential_id}</p>
								<p className="text-xs text-muted-foreground">{row.credential_id}</p>
								{row.used_by && row.used_by.length > 0 && (
									<p className="mt-1 text-xs text-muted-foreground">
										Used by {row.used_by.length} {row.used_by.length === 1 ? "skill" : "skills"}
									</p>
								)}
							</div>
							<ConnectBadge connected={row.connected} />
						</div>
					))}
				</div>
			)}
		</main>
	);
}

function ConnectBadge({
	connected,
}: {
	connected?: boolean | null;
}): React.ReactElement {
	if (connected === true) {
		return (
			<span className="rounded-full bg-primary/15 px-2.5 py-1 text-xs font-medium text-primary">
				Connected
			</span>
		);
	}
	if (connected === false) {
		return (
			<span className="rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground">
				Not connected
			</span>
		);
	}
	return (
		<span className="rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground">
			Global
		</span>
	);
}
