import { loadDots } from "@/lib/dots.config";
import Link from "next/link";
import { AuthorChat } from "./author-chat";

// Conversational on-ramp to the Dot Author. Loads the current Dot id list on the server so
// the chat component can watch for a newly-landed Dot and redirect without a race.

export default async function AuthorDotPage(): Promise<React.ReactElement> {
	const dots = await loadDots();
	const existingIds = dots.map((d) => d.id);
	return (
		<main className="mx-auto flex min-h-0 w-full max-w-screen-lg flex-1 flex-col px-6 pt-6 pb-0">
			<header className="pb-4">
				<h1 className="text-2xl font-semibold tracking-tight">Describe a new Dot</h1>
				<p className="mt-1 text-sm text-muted-foreground">
					Tell the Author what you want the Dot to do. It will design the topology, write the YAML
					into this workspace, and open the Dot when it's ready. For hand-filling the form instead,
					use the{" "}
					<Link href="/dots/new/manual" className="underline">
						manual add
					</Link>{" "}
					page.
				</p>
			</header>
			<AuthorChat existingIds={existingIds} />
		</main>
	);
}
