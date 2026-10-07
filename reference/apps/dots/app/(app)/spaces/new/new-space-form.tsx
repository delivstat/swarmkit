"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function NewSpaceForm(): React.ReactElement {
	const router = useRouter();
	const [id, setId] = useState("");
	const [name, setName] = useState("");
	const [description, setDescription] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	async function onSubmit(e: React.FormEvent): Promise<void> {
		e.preventDefault();
		setError(null);
		setSubmitting(true);
		try {
			const res = await fetch("/api/spaces", {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify({ id, name, description }),
			});
			if (!res.ok) {
				const body = (await res.json().catch(() => ({}))) as { detail?: string };
				setError(body.detail ?? `HTTP ${res.status}`);
				return;
			}
			router.push(`/spaces/${id}`);
			router.refresh();
		} finally {
			setSubmitting(false);
		}
	}

	const inputClass =
		"mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring";

	return (
		<form className="mt-6 space-y-4" onSubmit={onSubmit} noValidate>
			<div>
				<label htmlFor="id" className="block text-sm font-medium">
					Id
				</label>
				<input
					id="id"
					name="id"
					required
					pattern="^[a-z][a-z0-9-]*$"
					value={id}
					onChange={(e) => setId(e.target.value)}
					className={inputClass}
				/>
				<p className="mt-1 text-xs text-muted-foreground">
					Lowercase + hyphens. Becomes the Space's URL (/spaces/{"<id>"}) and the correlation_id
					(space:{"<id>"}) on every run inside it.
				</p>
			</div>
			<div>
				<label htmlFor="name" className="block text-sm font-medium">
					Name
				</label>
				<input
					id="name"
					name="name"
					required
					value={name}
					onChange={(e) => setName(e.target.value)}
					className={inputClass}
				/>
			</div>
			<div>
				<label htmlFor="description" className="block text-sm font-medium">
					Description (optional)
				</label>
				<textarea
					id="description"
					name="description"
					rows={2}
					value={description}
					onChange={(e) => setDescription(e.target.value)}
					className={inputClass}
				/>
			</div>
			{error ? (
				<p className="text-sm text-destructive" role="alert">
					{error}
				</p>
			) : null}
			<button
				type="submit"
				disabled={submitting}
				className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow disabled:opacity-60"
			>
				{submitting ? "Creating…" : "Create Space"}
			</button>
		</form>
	);
}
