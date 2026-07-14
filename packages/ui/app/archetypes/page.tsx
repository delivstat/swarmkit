"use client";

import { dump, load } from "js-yaml";
import { Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Card } from "@/components/card";
import { SchemaForm } from "@/components/schema-form";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { JsonSchema } from "@/lib/schema-form";
import type { ArchetypeDetail } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useRefOptions } from "@/lib/use-ref-options";
import { cn } from "@/lib/utils";

const NEW_ARCHETYPE_TEMPLATE = `apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: NAME
  name: ""
  description: "Description here (min 10 chars)."
role: worker
defaults:
  model:
    provider: openrouter
    name: moonshotai/kimi-k2.6
  prompt:
    system: |
      Your system prompt here.
  skills: []
provenance:
  authored_by: human
  version: 1.0.0
`;

function ArchetypeEditor({
	archetypeId,
	onClose,
	onSaved,
}: {
	archetypeId: string | null;
	onClose: () => void;
	onSaved: () => void;
}) {
	const [yaml, setYaml] = useState("");
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [isNew] = useState(!archetypeId);
	const [newName, setNewName] = useState("");
	const [, setDetail] = useState<ArchetypeDetail | null>(null);
	// Schema-driven form (with x-swarmkit-ref skill pickers) vs raw YAML.
	const [mode, setMode] = useState<"form" | "yaml">("form");
	const [obj, setObj] = useState<Record<string, unknown>>({});
	const [schema, setSchema] = useState<JsonSchema | null>(null);
	const refOptions = useRefOptions();

	const setBoth = (text: string) => {
		setYaml(text);
		try {
			setObj((load(text) as Record<string, unknown>) ?? {});
		} catch {
			// leave obj as-is on an unparseable draft
		}
	};

	useEffect(() => {
		api
			.schema("archetype")
			.then((s) => setSchema(s as JsonSchema))
			.catch(() => setSchema(null));
	}, []);

	useEffect(() => {
		if (archetypeId) {
			const base = process.env.NEXT_PUBLIC_SWARMKIT_API ?? "";
			Promise.all([
				api.archetypeDetail(archetypeId),
				fetch(`${base}/api/archetypes/${archetypeId}/yaml`).then((r) =>
					r.json(),
				),
			])
				.then(([d, yamlData]) => {
					setDetail(d);
					setBoth(yamlData.yaml ?? "");
					setLoading(false);
				})
				.catch(() => setLoading(false));
		} else {
			setBoth(NEW_ARCHETYPE_TEMPLATE);
			setLoading(false);
		}
	}, [archetypeId]);

	const handleSave = async () => {
		const id = isNew
			? newName
					.toLowerCase()
					.replace(/[^a-z0-9]+/g, "-")
					.replace(/^-|-$/g, "")
			: archetypeId;
		if (!id) {
			setError("Name is required");
			return;
		}
		setSaving(true);
		setError(null);
		try {
			let finalYaml: string;
			if (mode === "form") {
				const meta =
					(obj.metadata as Record<string, unknown> | undefined) ?? {};
				finalYaml = dump(isNew ? { ...obj, metadata: { ...meta, id } } : obj);
			} else {
				finalYaml = isNew ? yaml.replace("NAME", id) : yaml;
			}
			const result = await api.saveArchetype(id, finalYaml);
			if (result.valid) {
				await api.reloadWorkspace();
				onSaved();
			} else {
				setError(
					result.errors?.map((e) => e.message).join(", ") ??
						"Validation failed",
				);
			}
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setSaving(false);
		}
	};

	return (
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="max-h-[85vh] gap-3 overflow-y-auto sm:max-w-2xl">
				<DialogHeader>
					<DialogTitle>
						{isNew ? "New Archetype" : `Archetype: ${archetypeId}`}
					</DialogTitle>
				</DialogHeader>

				{isNew && (
					<div className="space-y-1.5">
						<Label htmlFor="arch-name">Archetype ID (kebab-case)</Label>
						<Input
							id="arch-name"
							placeholder="my-archetype"
							value={newName}
							onChange={(e) => setNewName(e.target.value)}
						/>
					</div>
				)}

				<div className="flex overflow-hidden rounded-md border text-xs">
					{(["form", "yaml"] as const).map((m) => (
						<button
							key={m}
							type="button"
							disabled={m === "form" && !schema}
							onClick={() => {
								setError(null);
								if (m === "yaml") {
									setYaml(dump(obj));
									setMode("yaml");
								} else {
									try {
										setObj((load(yaml) as Record<string, unknown>) ?? {});
										setMode("form");
									} catch {
										setError("YAML is invalid — fix it before using the form");
									}
								}
							}}
							className={cn(
								"px-3 py-1 font-medium transition-colors disabled:opacity-40",
								mode === m
									? "bg-accent text-accent-foreground"
									: "text-muted-foreground hover:bg-accent/50",
							)}
						>
							{m === "form" ? "Form" : "YAML"}
						</button>
					))}
				</div>

				{loading ? (
					<p className="text-sm text-muted-foreground">Loading…</p>
				) : mode === "form" && schema ? (
					<div className="max-h-[50vh] overflow-y-auto pr-1">
						<SchemaForm
							schema={schema}
							value={obj}
							onChange={setObj}
							options={refOptions}
						/>
					</div>
				) : (
					<Textarea
						className="min-h-[350px] font-mono text-xs"
						value={yaml}
						onChange={(e) => setBoth(e.target.value)}
						spellCheck={false}
					/>
				)}

				{error && <p className="text-xs text-destructive">{error}</p>}

				<DialogFooter>
					<Button type="button" variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button type="button" onClick={handleSave} disabled={saving}>
						{saving ? "Saving…" : "Save"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

export default function ArchetypesPage() {
	const fetchArchetypes = useCallback(() => api.archetypes(), []);
	const { data, error, loading, refetch } = usePoll<string[]>(
		fetchArchetypes,
		30000,
	);
	const [editing, setEditing] = useState<string | null | "new">(null);

	return (
		<div>
			<div className="mb-4 flex items-center justify-between">
				<h2 className="text-xl font-bold">Archetypes</h2>
				<Button type="button" size="sm" onClick={() => setEditing("new")}>
					<Plus size={12} /> New Archetype
				</Button>
			</div>
			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}
			{data && (
				<div className="grid grid-cols-3 gap-3">
					{data.map((name) => (
						<Card key={name}>
							<div className="flex items-center justify-between">
								<span className="font-medium">{name}</span>
								<Button
									type="button"
									variant="outline"
									size="sm"
									onClick={() => setEditing(name)}
								>
									View
								</Button>
							</div>
						</Card>
					))}
				</div>
			)}

			{editing !== null && (
				<ArchetypeEditor
					archetypeId={editing === "new" ? null : editing}
					onClose={() => setEditing(null)}
					onSaved={() => {
						setEditing(null);
						refetch();
					}}
				/>
			)}
		</div>
	);
}
