"use client";

import { Card, CardTitle } from "@/components/card";
import { api } from "@/lib/api";
import type { ArchetypeDetail } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

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
	const [detail, setDetail] = useState<ArchetypeDetail | null>(null);

	useEffect(() => {
		if (archetypeId) {
			const base =
				process.env.NEXT_PUBLIC_SWARMKIT_API ?? "http://localhost:8000";
			Promise.all([
				api.archetypeDetail(archetypeId),
				fetch(`${base}/api/archetypes/${archetypeId}/yaml`).then((r) =>
					r.json(),
				),
			])
				.then(([d, yamlData]) => {
					setDetail(d);
					setYaml(yamlData.yaml ?? "");
					setLoading(false);
				})
				.catch(() => setLoading(false));
		} else {
			setYaml(NEW_ARCHETYPE_TEMPLATE);
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
			const finalYaml = isNew ? yaml.replace("NAME", id) : yaml;
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
		<div
			className="fixed inset-0 flex items-center justify-center z-50"
			style={{ background: "rgba(0,0,0,0.5)" }}
		>
			<Card className="w-[600px] max-h-[80vh] overflow-y-auto">
				<CardTitle>
					{isNew ? "New Archetype" : `Archetype: ${archetypeId}`}
				</CardTitle>

				{isNew && (
					<div className="mb-3">
						<label
							htmlFor="arch-name"
							className="block text-sm mb-1"
							style={{ color: "var(--fg-muted)" }}
						>
							Archetype ID (kebab-case)
						</label>
						<input
							id="arch-name"
							className="w-full px-3 py-2 rounded border text-sm"
							style={{
								background: "var(--bg)",
								borderColor: "var(--border)",
								color: "var(--fg)",
							}}
							placeholder="my-archetype"
							value={newName}
							onChange={(e) => setNewName(e.target.value)}
						/>
					</div>
				)}

				{loading ? (
					<p className="text-sm opacity-50">Loading...</p>
				) : (
					<textarea
						className="w-full font-mono text-xs p-3 rounded border resize-none mb-3"
						style={{
							background: "var(--bg)",
							borderColor: "var(--border)",
							color: "var(--fg)",
							minHeight: "350px",
						}}
						value={yaml}
						onChange={(e) => setYaml(e.target.value)}
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

export default function ArchetypesPage() {
	const fetchArchetypes = useCallback(() => api.archetypes(), []);
	const { data, error, loading, refetch } = usePoll<string[]>(
		fetchArchetypes,
		30000,
	);
	const [editing, setEditing] = useState<string | null | "new">(null);

	return (
		<div>
			<div className="flex items-center justify-between mb-4">
				<h2 className="text-xl font-bold">Archetypes</h2>
				<button
					type="button"
					onClick={() => setEditing("new")}
					className="flex items-center gap-1 text-xs px-2.5 py-1 rounded font-medium"
					style={{
						background: "var(--accent)",
						color: "var(--accent-fg)",
					}}
				>
					<Plus size={12} />
					New Archetype
				</button>
			</div>
			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{data && (
				<div className="grid grid-cols-3 gap-3">
					{data.map((name) => (
						<Card key={name}>
							<div className="flex items-center justify-between">
								<span className="font-medium">{name}</span>
								<button
									type="button"
									onClick={() => setEditing(name)}
									className="text-xs px-2 py-0.5 rounded"
									style={{ border: "1px solid var(--border)" }}
								>
									View
								</button>
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
