import { LoginForm } from "./login-form";

export default async function Login({
	searchParams,
}: { searchParams: Promise<{ next?: string }> }) {
	const { next } = await searchParams;
	return (
		<main className="flex min-h-screen items-center justify-center px-4">
			<div className="w-full max-w-sm">
				<h1 className="text-2xl font-semibold tracking-tight">dots — sign in</h1>
				<p className="mt-1 text-sm text-muted-foreground">
					Owner-only. Credentials come from your <code>.env</code>.
				</p>
				<LoginForm next={next ?? "/"} />
			</div>
		</main>
	);
}
