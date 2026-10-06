import { headers } from "next/headers";
import Link from "next/link";
import { NewDotForm } from "./new-dot-form";

async function loadTopologies(): Promise<string[]> {
	const h = await headers();
	const host = h.get("host") ?? "127.0.0.1:3500";
	const protocol = h.get("x-forwarded-proto") ?? "http";
	try {
		const res = await fetch(`${protocol}://${host}/api/topologies`, {
			cache: "no-store",
		});
		if (!res.ok) return [];
		const body = (await res.json()) as { names?: string[] };
		return body.names ?? [];
	} catch {
		return [];
	}
}

export default async function NewDotPage(): Promise<React.ReactElement> {
	const topologies = await loadTopologies();
	return (
		<main className="mx-auto max-w-screen-md px-6 py-10">
			<h1 className="text-2xl font-semibold tracking-tight">Add a Dot (manual)</h1>
			<p className="mt-1 text-sm text-muted-foreground">
				Fill in the fields and point the Dot at a topology that already exists in the SwarmKit
				workspace. For a conversational on-ramp that writes the topology for you, use the{" "}
				<Link href="/dots/new/author" className="underline">
					Author
				</Link>{" "}
				page instead.
			</p>
			<NewDotForm topologies={topologies} />
		</main>
	);
}
