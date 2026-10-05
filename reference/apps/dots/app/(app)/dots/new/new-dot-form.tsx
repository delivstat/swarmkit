"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

const ICON_CHOICES = ["sunrise", "mail-reply", "github"] as const;

export function NewDotForm({
	topologies,
}: {
	topologies: string[];
}): React.ReactElement {
	const router = useRouter();
	const [id, setId] = useState("");
	const [name, setName] = useState("");
	const [role, setRole] = useState("");
	const [greeting, setGreeting] = useState("");
	const [topology, setTopology] = useState(topologies[0] ?? "");
	const [icon, setIcon] = useState<string>(ICON_CHOICES[0]);
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	async function onSubmit(e: React.FormEvent): Promise<void> {
		e.preventDefault();
		setError(null);
		setSubmitting(true);
		try {
			const res = await fetch("/api/dots", {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify({ id, name, role, greeting, topology, icon, renderers: [] }),
			});
			if (!res.ok) {
				const body = (await res.json().catch(() => ({}))) as { detail?: string };
				setError(body.detail ?? `HTTP ${res.status}`);
				return;
			}
			router.push(`/dots/${id}`);
			router.refresh();
		} finally {
			setSubmitting(false);
		}
	}

	const Field = (props: {
		label: string;
		id: string;
		children: React.ReactNode;
		hint?: string;
	}): React.ReactElement => (
		<div>
			<label htmlFor={props.id} className="block text-sm font-medium">
				{props.label}
			</label>
			{props.children}
			{props.hint ? <p className="mt-1 text-xs text-muted-foreground">{props.hint}</p> : null}
		</div>
	);

	const inputClass =
		"mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring";

	return (
		<form className="mt-6 space-y-4" onSubmit={onSubmit} noValidate>
			<Field label="Id" id="id" hint="Lowercase + hyphens. Used in the URL (/dots/<id>).">
				<input
					id="id"
					name="id"
					required
					pattern="^[a-z][a-z0-9-]*$"
					value={id}
					onChange={(e) => setId(e.target.value)}
					className={inputClass}
				/>
			</Field>
			<Field label="Name" id="name">
				<input
					id="name"
					name="name"
					required
					value={name}
					onChange={(e) => setName(e.target.value)}
					className={inputClass}
				/>
			</Field>
			<Field label="Role" id="role" hint="One line — what this Dot does.">
				<input
					id="role"
					name="role"
					required
					value={role}
					onChange={(e) => setRole(e.target.value)}
					className={inputClass}
				/>
			</Field>
			<Field label="Greeting" id="greeting" hint="The first message the Dot sends you.">
				<input
					id="greeting"
					name="greeting"
					required
					value={greeting}
					onChange={(e) => setGreeting(e.target.value)}
					className={inputClass}
				/>
			</Field>
			<Field
				label="Topology"
				id="topology"
				hint={
					topologies.length === 0
						? "No topologies reachable at SWARMKIT_URL — point it at a live swarmkit serve, or type the name manually."
						: "Must already exist in the SwarmKit workspace."
				}
			>
				{topologies.length === 0 ? (
					<input
						id="topology"
						name="topology"
						required
						value={topology}
						onChange={(e) => setTopology(e.target.value)}
						className={inputClass}
					/>
				) : (
					<select
						id="topology"
						name="topology"
						required
						value={topology}
						onChange={(e) => setTopology(e.target.value)}
						className={inputClass}
					>
						{topologies.map((t) => (
							<option key={t} value={t}>
								{t}
							</option>
						))}
					</select>
				)}
			</Field>
			<Field label="Icon" id="icon">
				<select
					id="icon"
					name="icon"
					value={icon}
					onChange={(e) => setIcon(e.target.value)}
					className={inputClass}
				>
					{ICON_CHOICES.map((c) => (
						<option key={c} value={c}>
							{c}
						</option>
					))}
				</select>
			</Field>
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
				{submitting ? "Adding…" : "Add Dot"}
			</button>
		</form>
	);
}
