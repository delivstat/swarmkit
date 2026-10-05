import { loadTemplates } from "@/lib/author-templates";
import { AuthorWizard, type TemplateSummary } from "./author-wizard";

export default async function AuthorPage(): Promise<React.ReactElement> {
	const templates: TemplateSummary[] = (await loadTemplates()).map((t) => ({
		id: t.id,
		name: t.name,
		summary: t.summary,
		params: t.params,
	}));
	return (
		<main className="mx-auto flex min-h-0 w-full max-w-2xl flex-1 flex-col px-6 py-8">
			<header>
				<h1 className="text-2xl font-semibold tracking-tight">Add a coworker</h1>
				<p className="mt-1 text-sm text-muted-foreground">
					Tell me what you want a new coworker to do. I&apos;ll pick the right recipe and wire
					everything up.
				</p>
			</header>
			<AuthorWizard templates={templates} />
		</main>
	);
}
