import { NewSpaceForm } from "./new-space-form";

export default function NewSpacePage(): React.ReactElement {
	return (
		<main className="mx-auto max-w-screen-md px-6 py-10">
			<h1 className="text-2xl font-semibold tracking-tight">New Space</h1>
			<p className="mt-1 text-sm text-muted-foreground">
				A Space groups runs across any Dot you chat with inside it. One URL per outcome — the thing
				you'd want to come back to tomorrow. If you'd never open its URL again, don't make one; just
				use a direct chat.
			</p>
			<NewSpaceForm />
		</main>
	);
}
