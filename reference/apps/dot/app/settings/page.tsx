// Settings — user-facing prefs projected from the workspace config. Scaffold; the form + PATCH
// wiring land in commit 7. See design/details/dot-app.md §5 (Settings).
export default function Settings() {
	return (
		<main className="mx-auto max-w-screen-md px-4 py-8 md:py-12">
			<h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
			<p className="mt-2 text-sm text-muted-foreground">
				Scaffold. Prefs form lands in commit 7.
			</p>
		</main>
	);
}
