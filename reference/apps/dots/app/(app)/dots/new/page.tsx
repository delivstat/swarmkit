import { headers } from "next/headers";
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
			<h1 className="text-2xl font-semibold tracking-tight">Add a Dot</h1>
			<p className="mt-1 text-sm text-muted-foreground">
				A Dot is a chat surface wired to one SwarmKit topology. Pick the topology it should talk to,
				name it, give it a role and a greeting — it shows up in the sidebar immediately.
			</p>
			<NewDotForm topologies={topologies} />
		</main>
	);
}
