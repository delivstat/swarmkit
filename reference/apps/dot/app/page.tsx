// Today's brief — the default authenticated route. Scaffold only in this commit; the card stack
// and refresh affordance land in the "brief static" commit. See design/details/dot-app.md §3.
export default function TodaysBrief() {
	return (
		<main className="mx-auto max-w-screen-md px-4 py-8 md:py-12">
			<h1 className="text-2xl font-semibold tracking-tight md:text-3xl">DOT</h1>
			<p className="mt-2 text-sm text-muted-foreground">
				Scaffold. Today's brief lands in commit 4 (per
				design/details/dot-app.md).
			</p>
		</main>
	);
}
