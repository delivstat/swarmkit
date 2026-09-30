// Per-item detail — item context + action buttons + scoped chat surface. Scaffold; the full
// interaction lands in commit 5. See design/details/dot-app.md §3.3.
export default async function ItemDetail({
	params,
}: { params: Promise<{ id: string }> }) {
	const { id } = await params;
	return (
		<main className="mx-auto max-w-screen-md px-4 py-8 md:py-12">
			<h1 className="text-2xl font-semibold tracking-tight">Item · {id}</h1>
			<p className="mt-2 text-sm text-muted-foreground">
				Scaffold. Action buttons + scoped chat land in commit 5.
			</p>
		</main>
	);
}
