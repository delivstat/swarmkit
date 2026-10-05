"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

export interface TemplateSummary {
	id: string;
	name: string;
	summary: string;
	params: Array<{ key: string; prompt: string; default?: string }>;
}

type TurnSide = "author" | "user";
interface Turn {
	id: string;
	side: TurnSide;
	text: string;
}

interface Draft {
	templateId?: string;
	dotName?: string;
	dotId?: string;
	params: Record<string, string>;
}

type Stage =
	| { kind: "pick-template" }
	| { kind: "name" }
	| { kind: "ask-param"; paramIndex: number }
	| { kind: "preview" }
	| { kind: "done"; dotId: string };

// A chat-shaped wizard. Each answered question becomes a message in the thread; the current
// question is rendered under the composer. Pure client-side state — commit happens once at
// the end via POST /api/dots/author.

export function AuthorWizard({
	templates,
}: {
	templates: TemplateSummary[];
}): React.ReactElement {
	const router = useRouter();
	const [turns, setTurns] = useState<Turn[]>([
		{
			id: "greet",
			side: "author",
			text:
				templates.length === 0
					? "No templates available yet. (Check that `reference/apps/dots/templates/` has at least one YAML.)"
					: "What do you want a new coworker to do? Pick a recipe below to start.",
		},
	]);
	const [draft, setDraft] = useState<Draft>({ params: {} });
	const [stage, setStage] = useState<Stage>({ kind: "pick-template" });
	const [input, setInput] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	const template = useMemo(
		() => templates.find((t) => t.id === draft.templateId),
		[templates, draft.templateId],
	);

	function appendTurn(side: TurnSide, text: string): void {
		setTurns((prev) => [...prev, { id: `${prev.length}`, side, text }]);
	}

	function pickTemplate(t: TemplateSummary): void {
		appendTurn("user", t.name);
		appendTurn("author", `${t.summary} Let's name it — what should I call it in the sidebar?`);
		setDraft((d) => ({ ...d, templateId: t.id }));
		setStage({ kind: "name" });
	}

	function submitName(): void {
		const name = input.trim();
		if (!name) return;
		const dotId = slugify(name);
		appendTurn("user", name);
		setDraft((d) => ({ ...d, dotName: name, dotId }));
		setInput("");
		if (!template) return;
		if (template.params.length === 0) {
			appendTurn("author", "All set. Here's what I'll create — ready?");
			setStage({ kind: "preview" });
			return;
		}
		const first = template.params[0];
		if (!first) {
			setStage({ kind: "preview" });
			return;
		}
		appendTurn("author", first.prompt);
		setStage({ kind: "ask-param", paramIndex: 0 });
	}

	function submitParam(idx: number): void {
		if (!template) return;
		const param = template.params[idx];
		if (!param) return;
		const value = input.trim() || param.default || "";
		appendTurn("user", value);
		setDraft((d) => ({ ...d, params: { ...d.params, [param.key]: value } }));
		setInput("");
		const next = idx + 1;
		if (next >= template.params.length) {
			appendTurn("author", "All set. Here's what I'll create — ready?");
			setStage({ kind: "preview" });
			return;
		}
		const nextParam = template.params[next];
		if (!nextParam) {
			setStage({ kind: "preview" });
			return;
		}
		appendTurn("author", nextParam.prompt);
		setStage({ kind: "ask-param", paramIndex: next });
	}

	async function confirm(): Promise<void> {
		if (!draft.templateId || !draft.dotId || !draft.dotName) return;
		setSubmitting(true);
		setError(null);
		try {
			const res = await fetch("/api/dots/author", {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify({
					templateId: draft.templateId,
					dotId: draft.dotId,
					dotName: draft.dotName,
					params: draft.params,
				}),
			});
			if (!res.ok) {
				const body = (await res.json().catch(() => ({}))) as { detail?: string };
				setError(body.detail ?? `HTTP ${res.status}`);
				return;
			}
			appendTurn("author", "Done. Opening the new Dot…");
			setStage({ kind: "done", dotId: draft.dotId });
			router.push(`/dots/${draft.dotId}`);
			router.refresh();
		} finally {
			setSubmitting(false);
		}
	}

	return (
		<div className="mt-6 flex min-h-0 flex-1 flex-col">
			<div className="flex-1 space-y-3 overflow-y-auto">
				{turns.map((t) => (
					<Bubble key={t.id} side={t.side} text={t.text} />
				))}
			</div>
			<div className="mt-4 border-t border-border pt-4">
				{stage.kind === "pick-template" ? (
					<div className="grid gap-2 sm:grid-cols-2">
						{templates.map((t) => (
							<button
								key={t.id}
								type="button"
								onClick={() => pickTemplate(t)}
								className="rounded-lg border border-border bg-muted/10 p-3 text-left transition-colors hover:bg-muted/20"
							>
								<p className="text-sm font-medium">{t.name}</p>
								<p className="mt-1 text-xs text-muted-foreground">{t.summary}</p>
							</button>
						))}
					</div>
				) : stage.kind === "preview" ? (
					<PreviewPanel
						draft={draft}
						templateName={template?.name ?? ""}
						error={error}
						submitting={submitting}
						onConfirm={confirm}
					/>
				) : stage.kind === "done" ? null : (
					<ComposerRow
						disabled={submitting}
						placeholder={stage.kind === "name" ? "e.g. Morning Brief" : "Type your answer…"}
						value={input}
						onChange={setInput}
						onSubmit={() => {
							if (stage.kind === "name") submitName();
							else if (stage.kind === "ask-param") submitParam(stage.paramIndex);
						}}
					/>
				)}
			</div>
		</div>
	);
}

function Bubble({
	side,
	text,
}: {
	side: TurnSide;
	text: string;
}): React.ReactElement {
	return (
		<div className={side === "user" ? "flex justify-end" : "flex justify-start"}>
			<div
				className={
					side === "user"
						? "max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
						: "max-w-[80%] rounded-lg bg-muted px-3 py-2 text-sm"
				}
			>
				{text}
			</div>
		</div>
	);
}

function ComposerRow({
	value,
	onChange,
	onSubmit,
	placeholder,
	disabled,
}: {
	value: string;
	onChange: (v: string) => void;
	onSubmit: () => void;
	placeholder: string;
	disabled?: boolean;
}): React.ReactElement {
	return (
		<form
			className="flex gap-2"
			onSubmit={(e) => {
				e.preventDefault();
				onSubmit();
			}}
		>
			<input
				className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
				placeholder={placeholder}
				value={value}
				onChange={(e) => onChange(e.target.value)}
				disabled={disabled}
			/>
			<button
				type="submit"
				className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
				disabled={disabled || !value.trim()}
			>
				Send
			</button>
		</form>
	);
}

function PreviewPanel({
	draft,
	templateName,
	error,
	submitting,
	onConfirm,
}: {
	draft: Draft;
	templateName: string;
	error: string | null;
	submitting: boolean;
	onConfirm: () => void;
}): React.ReactElement {
	return (
		<div className="rounded-lg border border-border bg-muted/10 p-4">
			<p className="text-sm font-medium">{draft.dotName}</p>
			<p className="text-xs text-muted-foreground">
				Recipe: {templateName} · id: <code>{draft.dotId}</code>
			</p>
			{Object.keys(draft.params).length > 0 ? (
				<dl className="mt-3 space-y-1 text-xs">
					{Object.entries(draft.params).map(([k, v]) => (
						<div key={k} className="flex justify-between gap-4">
							<dt className="text-muted-foreground">{k}</dt>
							<dd className="truncate">{v || <em>(blank)</em>}</dd>
						</div>
					))}
				</dl>
			) : null}
			{error ? (
				<p className="mt-3 text-sm text-destructive" role="alert">
					{error}
				</p>
			) : null}
			<button
				type="button"
				onClick={onConfirm}
				disabled={submitting}
				className="mt-4 w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow disabled:opacity-60"
			>
				{submitting ? "Creating…" : "Create coworker"}
			</button>
		</div>
	);
}

function slugify(input: string): string {
	return input
		.toLowerCase()
		.replace(/[^a-z0-9-]+/g, "-")
		.replace(/^-+|-+$/g, "")
		.replace(/^[0-9]/, (d) => `a${d}`);
}
