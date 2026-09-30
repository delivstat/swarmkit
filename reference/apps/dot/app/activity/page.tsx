// Activity — a thin projection of the runtime's audit log, owner-scoped. Scaffold; the list +
// detail lands in commit 8. See design/details/dot-app.md §6 (Activity).
export default function Activity() {
	return (
		<main className="mx-auto max-w-screen-md px-4 py-8 md:py-12">
			<h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
			<p className="mt-2 text-sm text-muted-foreground">
				Scaffold. Recent runs list lands in commit 8.
			</p>
		</main>
	);
}
