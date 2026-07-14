"use client";

import { dump, load } from "js-yaml";
import { Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Card } from "@/components/card";
import { SchemaForm } from "@/components/schema-form";
import { Badge } from "@/components/ui/badge";
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
import type { SkillItem } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useRefOptions } from "@/lib/use-ref-options";
import { cn } from "@/lib/utils";

const CATEGORY_COLORS: Record<string, string> = {
	capability: "text-sky-500",
	decision: "text-warning",
	coordination: "text-success",
	persistence: "text-muted-foreground",
};

const NEW_SKILL_TEMPLATE = `apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: NAME
  name: ""
  description: ""
category: capability
implementation:
  type: llm_prompt
  prompt: |
    Your prompt here.
provenance:
  authored_by: human
  version: 1.0.0
`;

function SkillEditor({
	skillId,
	onClose,
	onSaved,
}: {
	skillId: string | null;
	onClose: () => void;
	onSaved: () => void;
}) {
	const [yaml, setYaml] = useState("");
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [isNew, setIsNew] = useState(false);
	const [newName, setNewName] = useState("");
	// Form (schema-driven) vs raw YAML. The form is the designer: every field + tooltip from the
	// skill schema; YAML is the power/escape hatch. `obj` is the parsed skill for form mode.
	const [mode, setMode] = useState<"form" | "yaml">("form");
	const [obj, setObj] = useState<Record<string, unknown>>({});
	const [schema, setSchema] = useState<JsonSchema | null>(null);
	const refOptions = useRefOptions();

	const setBoth = (text: string) => {
		setYaml(text);
		try {
			setObj((load(text) as Record<string, unknown>) ?? {});
		} catch {
			// leave obj as-is; the form just won't reflect an unparseable draft
		}
	};

	useEffect(() => {
		api
			.schema("skill")
			.then((s) => setSchema(s as JsonSchema))
			.catch(() => setSchema(null));
	}, []);

	useEffect(() => {
		if (skillId) {
			const base = process.env.NEXT_PUBLIC_SWARMKIT_API ?? "";
			fetch(`${base}/api/skills/${skillId}/yaml`)
				.then((r) => r.json())
				.then((data) => {
					setBoth(data.yaml ?? "");
					setLoading(false);
				})
				.catch(() => setLoading(false));
		} else {
			setIsNew(true);
			setBoth(NEW_SKILL_TEMPLATE);
			setLoading(false);
		}
	}, [skillId]);

	const handleSave = async () => {
		const id = isNew
			? newName
					.toLowerCase()
					.replace(/[^a-z0-9]+/g, "-")
					.replace(/^-|-$/g, "")
			: skillId;
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
			const result = await api.saveSkill(id, finalYaml);
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
					<DialogTitle>{isNew ? "New Skill" : `Edit: ${skillId}`}</DialogTitle>
				</DialogHeader>

				{isNew && (
					<div className="space-y-1.5">
						<Label htmlFor="skill-name">Skill ID (kebab-case)</Label>
						<Input
							id="skill-name"
							placeholder="my-skill"
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
						className="min-h-[300px] font-mono text-xs"
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

export default function SkillsPage() {
	const fetchSkills = useCallback(() => api.skills(), []);
	const { data, error, loading, refetch } = usePoll<SkillItem[]>(
		fetchSkills,
		30000,
	);
	const [editingSkill, setEditingSkill] = useState<string | null | "new">(null);

	return (
		<div>
			<div className="mb-4 flex items-center justify-between">
				<h2 className="text-xl font-bold">Skills</h2>
				<Button type="button" size="sm" onClick={() => setEditingSkill("new")}>
					<Plus size={12} /> New Skill
				</Button>
			</div>
			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}
			{data && (
				<div className="grid grid-cols-2 gap-3">
					{data.map((skill) => (
						<Card key={skill.id}>
							<div className="flex items-center justify-between">
								<span className="font-medium">{skill.id}</span>
								<div className="flex items-center gap-2">
									<Badge
										variant="outline"
										className={cn(
											"border-current",
											CATEGORY_COLORS[skill.category] ??
												"text-muted-foreground",
										)}
									>
										{skill.category || "unknown"}
									</Badge>
									<Button
										type="button"
										variant="outline"
										size="sm"
										onClick={() => setEditingSkill(skill.id)}
									>
										View
									</Button>
								</div>
							</div>
						</Card>
					))}
				</div>
			)}

			{editingSkill !== null && (
				<SkillEditor
					skillId={editingSkill === "new" ? null : editingSkill}
					onClose={() => setEditingSkill(null)}
					onSaved={() => {
						setEditingSkill(null);
						refetch();
					}}
				/>
			)}
		</div>
	);
}
