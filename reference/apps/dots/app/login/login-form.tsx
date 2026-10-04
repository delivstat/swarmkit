"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function LoginForm({ next }: { next: string }) {
	const router = useRouter();
	const [username, setUsername] = useState("");
	const [password, setPassword] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
		e.preventDefault();
		setError(null);
		setSubmitting(true);
		try {
			const res = await fetch("/api/auth/login", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ username, password }),
			});
			if (!res.ok) {
				const body = (await res.json().catch(() => ({}))) as { error?: string };
				setError(errorMessage(body.error, res.status));
				return;
			}
			router.replace(next);
			router.refresh();
		} finally {
			setSubmitting(false);
		}
	}

	return (
		<form className="mt-6 space-y-4" onSubmit={onSubmit} noValidate>
			<div>
				<label className="block text-sm font-medium" htmlFor="username">
					Username
				</label>
				<input
					id="username"
					name="username"
					type="text"
					autoComplete="username"
					required
					value={username}
					onChange={(e) => setUsername(e.target.value)}
					className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
				/>
			</div>
			<div>
				<label className="block text-sm font-medium" htmlFor="password">
					Password
				</label>
				<input
					id="password"
					name="password"
					type="password"
					autoComplete="current-password"
					required
					value={password}
					onChange={(e) => setPassword(e.target.value)}
					className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
				/>
			</div>
			{error && (
				<p className="text-sm text-destructive" role="alert">
					{error}
				</p>
			)}
			<button
				type="submit"
				disabled={submitting}
				className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow disabled:opacity-60"
			>
				{submitting ? "Signing in…" : "Sign in"}
			</button>
		</form>
	);
}

function errorMessage(code: string | undefined, status: number): string {
	switch (code) {
		case "invalid_credentials":
			return "Incorrect username or password.";
		case "username_and_password_required":
			return "Both fields are required.";
		default:
			return status === 503 ? "Server not configured. Check your .env." : "Sign-in failed.";
	}
}
