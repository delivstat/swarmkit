"use client";

import { MODEL_PROVIDERS, type Settings } from "@/lib/settings";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

async function fetchSettings(): Promise<Settings> {
	const res = await fetch("/api/settings", { cache: "no-store" });
	if (!res.ok) throw new Error(`HTTP ${res.status}`);
	const body = (await res.json()) as { settings: Settings };
	return body.settings;
}

async function patchSettings(partial: Partial<Settings>): Promise<Settings> {
	const res = await fetch("/api/settings", {
		method: "PATCH",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(partial),
	});
	if (!res.ok) {
		const body = (await res.json().catch(() => ({}))) as { detail?: string };
		throw new Error(body.detail ?? `HTTP ${res.status}`);
	}
	const body = (await res.json()) as { settings: Settings };
	return body.settings;
}

export function SettingsView() {
	const qc = useQueryClient();
	const { data, isLoading, error } = useQuery({
		queryKey: ["settings"],
		queryFn: fetchSettings,
	});

	return (
		<main className="mx-auto max-w-screen-md px-4 py-6 md:py-10">
			<Link
				href="/"
				className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
			>
				<span aria-hidden>←</span> Back to brief
			</Link>
			<header className="mt-4">
				<h1 className="text-xl font-semibold tracking-tight md:text-2xl">
					Settings
				</h1>
				<p className="mt-1 text-sm text-muted-foreground">
					The prefs a normal day needs. Anything richer lives in the SwarmKit
					portal.
				</p>
			</header>
			{error && (
				<p className="mt-6 text-sm text-destructive">Couldn't load settings.</p>
			)}
			{isLoading && !data && (
				<div className="mt-6 h-64 animate-pulse rounded bg-muted" />
			)}
			{data && (
				<SettingsForm
					initial={data}
					onSaved={(fresh) => qc.setQueryData(["settings"], fresh)}
				/>
			)}
		</main>
	);
}

function SettingsForm({
	initial,
	onSaved,
}: {
	initial: Settings;
	onSaved: (s: Settings) => void;
}) {
	const [current, setCurrent] = useState(initial);
	const [savedAt, setSavedAt] = useState<number | null>(null);
	const [saveError, setSaveError] = useState<string | null>(null);
	const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const pendingRef = useRef<Partial<Settings>>({});

	const save = useMutation({
		mutationFn: patchSettings,
		onSuccess: (fresh) => {
			setSavedAt(Date.now());
			setSaveError(null);
			setCurrent(fresh);
			onSaved(fresh);
		},
		onError: (e: Error) => setSaveError(e.message),
	});

	// Debounce: coalesce field edits for 600 ms, then flush the merged patch.
	function schedule(patch: Partial<Settings>) {
		pendingRef.current = { ...pendingRef.current, ...patch };
		setCurrent((prev) => ({ ...prev, ...patch }));
		if (timer.current) clearTimeout(timer.current);
		timer.current = setTimeout(() => {
			const p = pendingRef.current;
			pendingRef.current = {};
			save.mutate(p);
		}, 600);
	}

	useEffect(() => {
		return () => {
			if (timer.current) clearTimeout(timer.current);
		};
	}, []);

	return (
		<section className="mt-6 space-y-6">
			<Group title="Morning brief" hint="When the scheduled run fires.">
				<Field label="Time (local)">
					<input
						type="time"
						value={current.briefTime}
						onChange={(e) => schedule({ briefTime: e.target.value })}
						className={inputClass}
					/>
				</Field>
				<Field label="Timezone (IANA)">
					<input
						type="text"
						value={current.briefTimezone}
						onChange={(e) => schedule({ briefTimezone: e.target.value })}
						placeholder="e.g. America/Los_Angeles"
						className={inputClass}
					/>
				</Field>
			</Group>
			<Group
				title="Model"
				hint="Applies to aggregator, ranker, drafter, prepper."
			>
				<Field label="Provider">
					<select
						value={current.modelProvider}
						onChange={(e) => schedule({ modelProvider: e.target.value })}
						className={inputClass}
					>
						{MODEL_PROVIDERS.map((p) => (
							<option key={p.id} value={p.id}>
								{p.label}
							</option>
						))}
					</select>
				</Field>
				<Field label="Model name">
					<input
						type="text"
						value={current.modelName}
						onChange={(e) => schedule({ modelName: e.target.value })}
						className={inputClass}
					/>
				</Field>
			</Group>
			<Group title="Ranker" hint="Max items in a daily brief.">
				<Field label="Item cap">
					<input
						type="number"
						min={1}
						max={30}
						value={current.rankerItemCap}
						onChange={(e) =>
							schedule({ rankerItemCap: Number(e.target.value) })
						}
						className={inputClass}
					/>
				</Field>
			</Group>
			<footer className="flex items-center gap-3 text-sm">
				{save.isPending ? (
					<span className="text-muted-foreground">Saving…</span>
				) : saveError ? (
					<span className="text-destructive">{saveError}</span>
				) : savedAt ? (
					<span className="text-muted-foreground">Saved · just now</span>
				) : (
					<span className="text-muted-foreground">
						Changes save automatically.
					</span>
				)}
			</footer>
		</section>
	);
}

const inputClass =
	"block w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring";

function Group({
	title,
	hint,
	children,
}: { title: string; hint?: string; children: React.ReactNode }) {
	return (
		<section className="rounded-lg border border-border bg-card p-4 shadow-sm">
			<h2 className="text-base font-semibold">{title}</h2>
			{hint && <p className="mt-1 text-sm text-muted-foreground">{hint}</p>}
			<div className="mt-4 space-y-4 md:grid md:grid-cols-2 md:gap-4 md:space-y-0">
				{children}
			</div>
		</section>
	);
}

function Field({
	label,
	children,
}: { label: string; children: React.ReactNode }) {
	return (
		<div className="block">
			<div className="mb-1 block text-sm font-medium">{label}</div>
			{children}
		</div>
	);
}
