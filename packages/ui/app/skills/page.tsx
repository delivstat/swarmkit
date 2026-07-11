"use client";

import { Card, CardTitle } from "@/components/card";
import { SchemaForm } from "@/components/schema-form";
import { api } from "@/lib/api";
import type { JsonSchema } from "@/lib/schema-form";
import type { SkillDetail, SkillItem } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useRefOptions } from "@/lib/use-ref-options";
import { dump, load } from "js-yaml";
import { Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

const CATEGORY_COLORS: Record<string, string> = {
	capability: "var(--accent)",
	decision: "var(--warning)",
	coordination: "var(--success)",
	persistence: "var(--fg-muted)",
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
			const base =
				process.env.NEXT_PUBLIC_SWARMKIT_API ?? "http://localhost:8000";
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
		<div
			className="fixed inset-0 flex items-center justify-center z-50"
			style={{ background: "rgba(0,0,0,0.5)" }}
		>
			<Card className="w-[600px] max-h-[80vh] overflow-y-auto">
				<CardTitle>{isNew ? "New Skill" : `Edit: ${skillId}`}</CardTitle>

				{isNew && (
					<div className="mb-3">
						<label
							htmlFor="skill-name"
							className="block text-sm mb-1"
							style={{ color: "var(--fg-muted)" }}
						>
							Skill ID (kebab-case)
						</label>
						<input
							id="skill-name"
							className="w-full px-3 py-2 rounded border text-sm"
							style={{
								background: "var(--bg)",
								borderColor: "var(--border)",
								color: "var(--fg)",
							}}
							placeholder="my-skill"
							value={newName}
							onChange={(e) => setNewName(e.target.value)}
						/>
					</div>
				)}

				<div className="mb-3 flex gap-1 text-xs">
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
							className="rounded px-2 py-1 font-medium disabled:opacity-40"
							style={{
								background: mode === m ? "var(--accent)" : "transparent",
								color: mode === m ? "var(--accent-fg)" : "var(--fg-muted)",
								border: "1px solid var(--border)",
							}}
						>
							{m === "form" ? "Form" : "YAML"}
						</button>
					))}
				</div>

				{loading ? (
					<p className="text-sm opacity-50">Loading...</p>
				) : mode === "form" && schema ? (
					<div className="mb-3 max-h-[50vh] overflow-y-auto pr-1">
						<SchemaForm
							schema={schema}
							value={obj}
							onChange={setObj}
							options={refOptions}
						/>
					</div>
				) : (
					<textarea
						className="w-full font-mono text-xs p-3 rounded border resize-none mb-3"
						style={{
							background: "var(--bg)",
							borderColor: "var(--border)",
							color: "var(--fg)",
							minHeight: "300px",
						}}
						value={yaml}
						onChange={(e) => setBoth(e.target.value)}
						spellCheck={false}
					/>
				)}

				{error && (
					<p className="text-xs mb-3" style={{ color: "var(--error)" }}>
						{error}
					</p>
				)}

				<div className="flex gap-2 justify-end">
					<button
						type="button"
						onClick={onClose}
						className="px-3 py-1.5 text-sm rounded border"
						style={{ borderColor: "var(--border)" }}
					>
						Cancel
					</button>
					<button
						type="button"
						onClick={handleSave}
						disabled={saving}
						className="px-3 py-1.5 text-sm rounded font-medium disabled:opacity-40"
						style={{
							background: "var(--accent)",
							color: "var(--accent-fg)",
						}}
					>
						{saving ? "Saving..." : "Save"}
					</button>
				</div>
			</Card>
		</div>
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
			<div className="flex items-center justify-between mb-4">
				<h2 className="text-xl font-bold">Skills</h2>
				<button
					type="button"
					onClick={() => setEditingSkill("new")}
					className="flex items-center gap-1 text-xs px-2.5 py-1 rounded font-medium"
					style={{
						background: "var(--accent)",
						color: "var(--accent-fg)",
					}}
				>
					<Plus size={12} />
					New Skill
				</button>
			</div>
			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{data && (
				<div className="grid grid-cols-2 gap-3">
					{data.map((skill) => (
						<Card key={skill.id}>
							<div className="flex items-center justify-between">
								<span className="font-medium">{skill.id}</span>
								<div className="flex items-center gap-2">
									<span
										className="text-xs px-2 py-0.5 rounded-full border"
										style={{
											color:
												CATEGORY_COLORS[skill.category] ?? "var(--fg-muted)",
											borderColor: `${CATEGORY_COLORS[skill.category] ?? "var(--fg-muted)"}40`,
										}}
									>
										{skill.category || "unknown"}
									</span>
									<button
										type="button"
										onClick={() => setEditingSkill(skill.id)}
										className="text-xs px-2 py-0.5 rounded"
										style={{ border: "1px solid var(--border)" }}
									>
										View
									</button>
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
