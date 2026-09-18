"use client";

import { useCallback, useEffect, useState } from "react";

import { Card } from "@/components/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type {
	SkillAddResult,
	SkillCatalogue,
	SkillCheckRow,
} from "@/lib/types";

/**
 * The catalogue half of the Skills page — `swarmkit skill search|add|import|check` as a peer of
 * the CLI (design/details/skill-registry.md). Adding shows both fragments (the skill file and
 * the `mcp_servers` entry) before anything is written, the way the CLI asks.
 */
export function SkillLibrary({ onChanged }: { onChanged: () => void }) {
	const [query, setQuery] = useState("");
	const [cat, setCat] = useState<SkillCatalogue | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [plan, setPlan] = useState<{
		ref: string;
		result: SkillAddResult;
	} | null>(null);
	const [busy, setBusy] = useState<string | null>(null);
	const [message, setMessage] = useState<string | null>(null);

	const search = useCallback(async (q: string, refresh = false) => {
		setLoading(true);
		setError(null);
		try {
			setCat(await api.skillCatalogue(q, refresh));
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		void search("");
	}, [search]);

	async function preview(ref: string) {
		setBusy(ref);
		setMessage(null);
		try {
			setPlan({ ref, result: await api.addSkill(ref, true) });
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(null);
		}
	}

	async function apply() {
		if (!plan) return;
		setBusy(plan.ref);
		try {
			const done = await api.addSkill(plan.ref, false);
			const files = done.applied?.skill_files.join(", ") || "no new files";
			setMessage(
				`wrote ${files}${done.applied?.server ? " and updated mcp_servers" : ""}.`,
			);
			setPlan(null);
			onChanged();
			await search(query);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(null);
		}
	}

	return (
		<div className="space-y-4">
			<form
				className="flex gap-2"
				onSubmit={(e) => {
					e.preventDefault();
					void search(query);
				}}
			>
				<Input
					aria-label="Search the catalogue"
					placeholder="Search the catalogue — e.g. git history, spreadsheet, browser"
					value={query}
					onChange={(e) => setQuery(e.target.value)}
				/>
				<Button type="submit" size="sm" disabled={loading}>
					Search
				</Button>
				<Button
					type="button"
					size="sm"
					variant="outline"
					disabled={loading}
					onClick={() => void search(query, true)}
					title="Refetch the catalogue (it is cached for a day)"
				>
					Refresh
				</Button>
			</form>
			{cat ? (
				<p className="text-xs text-muted-foreground">
					catalogue: <code>{cat.source}</code> · {cat.bundles.length} bundles ·{" "}
					{cat.skills.length} skills
				</p>
			) : null}
			{error ? <p className="text-sm text-destructive">{error}</p> : null}
			{message ? <p className="text-sm">{message}</p> : null}

			{plan ? (
				<Card>
					<div className="mb-2 flex items-center justify-between">
						<span className="font-medium">
							Add <code>{plan.ref}</code> — this writes:
						</span>
						<div className="flex gap-2">
							<Button
								type="button"
								size="sm"
								onClick={() => void apply()}
								disabled={busy !== null}
							>
								{plan.result.server_entry && plan.result.server_state !== "same"
									? "Write files and edit workspace.yaml"
									: "Write files"}
							</Button>
							<Button
								type="button"
								size="sm"
								variant="outline"
								onClick={() => setPlan(null)}
							>
								Cancel
							</Button>
						</div>
					</div>
					{plan.result.notes.map((n) => (
						<p key={n} className="text-xs text-muted-foreground">
							{n}
						</p>
					))}
					{Object.entries(plan.result.skill_files).map(([path, text]) => (
						<details key={path} className="mt-2" open>
							<summary className="cursor-pointer font-mono text-xs">
								{path}
							</summary>
							<pre className="mt-1 max-h-64 overflow-auto rounded bg-muted p-2 text-xs">
								{text}
							</pre>
						</details>
					))}
					{plan.result.server_entry ? (
						<details className="mt-2" open>
							<summary className="cursor-pointer font-mono text-xs">
								workspace.yaml · mcp_servers ·{" "}
								{
									{
										new: "new entry",
										same: "already there, unchanged",
										differs: "REPLACES the existing entry",
									}[plan.result.server_state]
								}
							</summary>
							<pre className="mt-1 max-h-64 overflow-auto rounded bg-muted p-2 text-xs">
								{plan.result.server_fragment}
							</pre>
						</details>
					) : null}
				</Card>
			) : null}

			{cat ? (
				<div className="grid grid-cols-2 gap-3">
					{cat.skills.map((s) => {
						const bundle = cat.bundles.find((b) => b.id === s.bundle);
						const v = bundle?.verification;
						return (
							<Card key={s.id}>
								<div className="flex items-start justify-between gap-2">
									<div className="min-w-0">
										<div className="flex items-center gap-2">
											<span className="font-medium">{s.id}</span>
											<Badge variant="outline">{s.bundle}</Badge>
											{s.installed ? <Badge>installed</Badge> : null}
										</div>
										<p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
											{s.description}
										</p>
										{v?.state ? (
											<p className="mt-1 text-xs text-muted-foreground">
												{v.state}
												{v.checked_at ? ` · checked ${v.checked_at}` : ""}
											</p>
										) : null}
									</div>
									<Button
										type="button"
										size="sm"
										variant={s.installed ? "outline" : "default"}
										disabled={busy !== null}
										onClick={() => void preview(s.id)}
									>
										{s.installed ? "Re-add" : "Add"}
									</Button>
								</div>
							</Card>
						);
					})}
					{cat.skills.length === 0 && !loading ? (
						<p className="text-sm text-muted-foreground">Nothing matches.</p>
					) : null}
				</div>
			) : null}

			<ImportSkillMd onChanged={onChanged} />
			<CheckSkills />
		</div>
	);
}

function ImportSkillMd({ onChanged }: { onChanged: () => void }) {
	const [text, setText] = useState("");
	const [origin, setOrigin] = useState("");
	const [result, setResult] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);

	async function run(dryRun: boolean) {
		setError(null);
		try {
			const r = await api.importSkill(text, origin || "upload", dryRun);
			setResult(dryRun ? JSON.stringify(r.skill, null, 2) : `wrote ${r.path}.`);
			if (!dryRun) {
				onChanged();
				setText("");
			}
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		}
	}

	return (
		<Card>
			<h3 className="mb-1 font-medium">Import a SKILL.md</h3>
			<p className="mb-2 text-xs text-muted-foreground">
				An Agent Skills file (frontmatter + instructions) becomes an{" "}
				<code>llm_prompt</code> skill; the body is the prompt, verbatim.
			</p>
			<Input
				aria-label="Origin"
				placeholder="Origin (a URL or repo path, kept in provenance.registry)"
				value={origin}
				onChange={(e) => setOrigin(e.target.value)}
				className="mb-2"
			/>
			<Textarea
				aria-label="SKILL.md"
				placeholder={
					"---\nname: brand-voice\ndescription: …\n---\n# Instructions…"
				}
				value={text}
				onChange={(e) => setText(e.target.value)}
				rows={6}
			/>
			<div className="mt-2 flex gap-2">
				<Button
					type="button"
					size="sm"
					variant="outline"
					disabled={!text}
					onClick={() => void run(true)}
				>
					Preview
				</Button>
				<Button
					type="button"
					size="sm"
					disabled={!text}
					onClick={() => void run(false)}
				>
					Import
				</Button>
			</div>
			{error ? <p className="mt-2 text-sm text-destructive">{error}</p> : null}
			{result ? (
				<pre className="mt-2 max-h-64 overflow-auto rounded bg-muted p-2 text-xs">
					{result}
				</pre>
			) : null}
		</Card>
	);
}

function CheckSkills() {
	const [rows, setRows] = useState<SkillCheckRow[] | null>(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);

	async function run() {
		setBusy(true);
		setError(null);
		try {
			setRows(await api.checkSkills());
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setBusy(false);
		}
	}

	const tone: Record<SkillCheckRow["status"], string> = {
		ok: "text-green-500",
		missing: "text-amber-500",
		"server-failed": "text-destructive",
	};
	return (
		<Card>
			<div className="flex items-center justify-between">
				<div>
					<h3 className="font-medium">Check the workspace&apos;s tools</h3>
					<p className="text-xs text-muted-foreground">
						Starts each <code>mcp_tool</code> skill&apos;s server the way a run
						would and asks whether the tool is still there.
					</p>
				</div>
				<Button
					type="button"
					size="sm"
					variant="outline"
					disabled={busy}
					onClick={() => void run()}
				>
					{busy ? "Checking…" : "Check"}
				</Button>
			</div>
			{error ? <p className="mt-2 text-sm text-destructive">{error}</p> : null}
			{rows ? (
				<ul
					className="mt-2 space-y-1 font-mono text-xs"
					data-testid="skill-check"
				>
					{rows.length === 0 ? <li>no mcp_tool skills to check.</li> : null}
					{rows.map((r) => (
						<li key={r.skill_id}>
							<span className={tone[r.status]}>{r.status.padEnd(13)}</span>{" "}
							{r.skill_id}{" "}
							<span className="text-muted-foreground">
								{r.server_id}:{r.tool}
								{r.detail ? ` — ${r.detail}` : ""}
							</span>
						</li>
					))}
				</ul>
			) : null}
		</Card>
	);
}
