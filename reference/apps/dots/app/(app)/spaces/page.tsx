import Link from "next/link";

import { loadSpaces } from "@/lib/spaces.config";

// Spaces index — list every Space, newest-first.

export default async function SpacesPage(): Promise<React.ReactElement> {
	const spaces = (await loadSpaces()).sort((a, b) => b.created_at.localeCompare(a.created_at));

	return (
		<main className="mx-auto max-w-screen-md px-6 py-10">
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-2xl font-semibold tracking-tight">Spaces</h1>
					<p className="mt-1 text-sm text-muted-foreground">
						Each Space is a scope that groups runs across any Dot you talk to inside it — one URL to
						come back to, one activity timeline, one approval queue.
					</p>
				</div>
				<Link
					href="/spaces/new"
					className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow"
				>
					New Space
				</Link>
			</div>

			{spaces.length === 0 ? (
				<div className="mt-10 rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
					No Spaces yet. A Space is a scope broader than one chat — "launch Q4 campaign", "refactor
					auth module", or whatever outcome you'll come back to.
				</div>
			) : (
				<ul className="mt-6 divide-y divide-border rounded-lg border border-border">
					{spaces.map((s) => (
						<li key={s.id}>
							<Link href={`/spaces/${s.id}`} className="block p-4 transition hover:bg-muted/40">
								<div className="font-medium">{s.name}</div>
								{s.description ? (
									<div className="mt-1 text-sm text-muted-foreground">{s.description}</div>
								) : null}
								<div className="mt-2 text-xs text-muted-foreground">
									{s.id} · owner {s.owner} · created {s.created_at.slice(0, 10)}
								</div>
							</Link>
						</li>
					))}
				</ul>
			)}
		</main>
	);
}
