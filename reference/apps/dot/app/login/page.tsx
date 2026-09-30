// Login — the unauthenticated entry point. Scaffold; the form + POST /api/auth/login land in
// commit 2. See design/details/dot-app.md §3.1.
export default function Login() {
	return (
		<main className="flex min-h-screen items-center justify-center px-4">
			<div className="w-full max-w-sm">
				<h1 className="text-2xl font-semibold tracking-tight">DOT — sign in</h1>
				<p className="mt-2 text-sm text-muted-foreground">
					Scaffold. Form lands in commit 2.
				</p>
			</div>
		</main>
	);
}
