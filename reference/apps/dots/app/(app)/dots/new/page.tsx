import Link from "next/link";

// Add-a-Dot entry page. Two ways in:
//   /dots/new/author — chat with the Dot Author (writes the topology YAML for you)
//   /dots/new/manual — fill the form by hand (point at an existing topology)
// Both end up registering a Dot entry via /api/dots or /api/dots/intake and opening /dots/<id>.

export default function NewDotPage(): React.ReactElement {
	return (
		<main className="mx-auto max-w-screen-md px-6 py-10">
			<h1 className="text-2xl font-semibold tracking-tight">Add a Dot</h1>
			<p className="mt-1 text-sm text-muted-foreground">
				A Dot is a chat surface wired to one SwarmKit topology. Pick how you want to create it:
			</p>
			<div className="mt-6 grid gap-4 sm:grid-cols-2">
				<Link
					href="/dots/new/author"
					className="rounded-lg border border-border p-5 transition hover:bg-muted/40"
				>
					<h2 className="font-medium">Describe it</h2>
					<p className="mt-1 text-sm text-muted-foreground">
						Chat with the Dot Author. Explain what you want and it writes the topology YAML into the
						workspace, then opens the Dot.
					</p>
				</Link>
				<Link
					href="/dots/new/manual"
					className="rounded-lg border border-border p-5 transition hover:bg-muted/40"
				>
					<h2 className="font-medium">Fill the form</h2>
					<p className="mt-1 text-sm text-muted-foreground">
						Point a Dot at a topology that already exists in the SwarmKit workspace. Faster when
						you've already written the YAML yourself.
					</p>
				</Link>
			</div>
		</main>
	);
}
