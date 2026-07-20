"use client";

// The Funnels surface (design/details/gate-funnel.md): author/inspect Funnel artifacts — reusable
// per-artifact quality gates. Mirrors how skills/archetypes are surfaced (list + editor), but the
// primary editor is the STRUCTURED PIPELINE canvas: the fixed draft→validate→judge→review→approve
// gate with per-layer config panels and optional-layer toggles. The graph is not user-rewireable.
//
// Defensive by design: the runtime already serves the funnel schema (GET /api/schema/funnel) and
// `@swarmkit/schema` validates locally, but the funnel CRUD routes (/funnels, /api/funnels/{id})
// may not be wired yet — the list gates gracefully to empty and a save that the runtime rejects
// surfaces a clear "not persisted" notice rather than failing silently.

import { dump, load } from "js-yaml";
import { Funnel, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { FunnelCanvas } from "@/components/funnel-canvas";
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
import type { FunnelLayerKey, OptionalLayerKey } from "@/lib/funnel-graph";
import { type JsonSchema, normalizeSchema } from "@/lib/schema-form";
import { useRefOptions } from "@/lib/use-ref-options";
import { cn } from "@/lib/utils";

const NEW_FUNNEL_TEMPLATE = `apiVersion: swarmkit/v1
kind: Funnel
metadata:
  id: NAME
  name: ""
  description: "Reusable quality gate (min 10 chars)."
approve:
  rules:
    - scope: artifact:approve
      roles:
        - reviewer
      quorum: any
  exclude_author: true
  on_revision: reset_all
provenance:
  authored_by: human
  version: 1.0.0
`;

/** Seed blocks a toggled-on optional layer starts from (kept minimal — the config panel renders the
 * rest of the schema's fields; the required field is present so validation guides the user). */
const LAYER_DEFAULTS: Record<OptionalLayerKey, Record<string, unknown>> = {
	validate: { autocorrect: true },
	judge: { skill: "" },
	review: { archetype: "" },
};

type EditorMode = "canvas" | "form" | "yaml";

/** The (normalized) sub-schema for one funnel property, resolved against the funnel document schema
 * so its `$ref`s to `$defs` (approval_policy, rule, quorum, …) resolve. */
function subSchema(root: JsonSchema | null, key: string): JsonSchema | null {
	if (!root) return null;
	const props = root.properties as Record<string, JsonSchema> | undefined;
	const node = props?.[key];
	return node ? normalizeSchema(root, node) : null;
}

function NewFunnelDialog({
	onClose,
	onCreate,
}: {
	onClose: () => void;
	onCreate: (id: string, obj: Record<string, unknown>) => void;
}) {
	const [name, setName] = useState("");
	const [error, setError] = useState<string | null>(null);

	const handleCreate = () => {
		const slug = name
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-|-$/g, "");
		if (!slug) {
			setError("Name must contain at least one letter");
			return;
		}
		try {
			const obj = load(NEW_FUNNEL_TEMPLATE.replace("NAME", slug)) as Record<
				string,
				unknown
			>;
			onCreate(slug, obj);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		}
	};

	return (
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>New Funnel</DialogTitle>
				</DialogHeader>
				<div className="space-y-1.5">
					<Label htmlFor="funnel-name">Funnel ID (kebab-case)</Label>
					<Input
						id="funnel-name"
						placeholder="artifact-quality-gate"
						value={name}
						onChange={(e) => setName(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === "Enter") handleCreate();
						}}
					/>
					{error && <p className="text-xs text-destructive">{error}</p>}
				</div>
				<DialogFooter>
					<Button type="button" variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button type="button" onClick={handleCreate} disabled={!name.trim()}>
						Create
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

/** The per-layer / details config panel — a schema-driven form for whichever layer is selected. */
function LayerConfig({
	schema,
	selectedLayer,
	obj,
	setLayer,
	options,
}: {
	schema: JsonSchema | null;
	selectedLayer: FunnelLayerKey | null;
	obj: Record<string, unknown>;
	setLayer: (key: string, value: Record<string, unknown>) => void;
	options: Record<string, string[]>;
}) {
	if (!schema) {
		return (
			<p className="p-4 text-sm text-muted-foreground">
				Loading funnel schema…
			</p>
		);
	}
	// Structural anchors carry no config.
	if (selectedLayer === "draft" || selectedLayer === "done") {
		return (
			<div className="p-4 text-sm text-muted-foreground">
				{selectedLayer === "draft"
					? "The drafting agent's output enters the funnel here — there is nothing to configure."
					: "The artifact is released here, only after the human approve gate advances."}
			</div>
		);
	}
	// Funnel details: metadata + provenance.
	if (selectedLayer === null) {
		const metaSchema = subSchema(schema, "metadata");
		const provSchema = subSchema(schema, "provenance");
		return (
			<div className="space-y-4 p-4">
				<div>
					<h4 className="mb-2 text-sm font-semibold">Metadata</h4>
					{metaSchema && (
						<SchemaForm
							root={schema}
							schema={metaSchema}
							value={(obj.metadata as Record<string, unknown>) ?? {}}
							onChange={(v) => setLayer("metadata", v)}
							options={options}
						/>
					)}
				</div>
				<div>
					<h4 className="mb-2 text-sm font-semibold">Provenance</h4>
					{provSchema && (
						<SchemaForm
							root={schema}
							schema={provSchema}
							value={(obj.provenance as Record<string, unknown>) ?? {}}
							onChange={(v) => setLayer("provenance", v)}
							options={options}
						/>
					)}
				</div>
			</div>
		);
	}
	// An automated / approve layer.
	const layerSchema = subSchema(schema, selectedLayer);
	const present =
		typeof obj[selectedLayer] === "object" && obj[selectedLayer] !== null;
	if (!present) {
		return (
			<div className="p-4 text-sm text-muted-foreground">
				The <span className="font-medium">{selectedLayer}</span> layer is off.
				Toggle it on in the canvas to configure it.
			</div>
		);
	}
	return (
		<div className="p-4">
			<h4 className="mb-2 text-sm font-semibold capitalize">
				{selectedLayer} layer
			</h4>
			{layerSchema && (
				<SchemaForm
					root={schema}
					schema={layerSchema}
					value={(obj[selectedLayer] as Record<string, unknown>) ?? {}}
					onChange={(v) => setLayer(selectedLayer, v)}
					options={options}
				/>
			)}
		</div>
	);
}

export default function FunnelsPage() {
	const [schema, setSchema] = useState<JsonSchema | null>(null);
	const [names, setNames] = useState<string[]>([]);
	const [listUnavailable, setListUnavailable] = useState(false);
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [obj, setObj] = useState<Record<string, unknown>>({});
	const [savedObj, setSavedObj] = useState<Record<string, unknown> | null>(
		null,
	);
	const [yamlDraft, setYamlDraft] = useState("");
	const [mode, setMode] = useState<EditorMode>("canvas");
	const [selectedLayer, setSelectedLayer] = useState<FunnelLayerKey | null>(
		null,
	);
	const [showNew, setShowNew] = useState(false);
	const [saving, setSaving] = useState(false);
	const [notice, setNotice] = useState<string | null>(null);
	const refOptions = useRefOptions();

	// The funnel schema drives the form + per-layer panels. The runtime serves it at
	// GET /api/schema/funnel (SchemaName includes "funnel"); a failure leaves the form gated.
	useEffect(() => {
		api
			.schema("funnel")
			.then((s) => setSchema(s as JsonSchema))
			.catch(() => setSchema(null));
	}, []);

	const loadList = useCallback(() => {
		api
			.funnels()
			.then((n) => {
				setNames(n);
				setListUnavailable(false);
			})
			.catch(() => {
				setNames([]);
				setListUnavailable(true);
			});
	}, []);
	useEffect(() => loadList(), [loadList]);

	const dirty = useMemo(
		() => savedObj !== null && dump(obj) !== dump(savedObj),
		[obj, savedObj],
	);

	const openFunnel = (id: string, next: Record<string, unknown>) => {
		setSelectedId(id);
		setObj(next);
		setSavedObj(next);
		setYamlDraft(dump(next));
		setSelectedLayer(null);
		setNotice(null);
	};

	const loadFunnel = (id: string) => {
		api
			.funnelYaml(id)
			.then((r) => {
				const parsed = (load(r.yaml) as Record<string, unknown>) ?? {};
				openFunnel(id, parsed);
			})
			.catch((err) =>
				setNotice(
					`Could not load funnel "${id}" (the runtime may not serve /api/funnels yet): ${
						err instanceof Error ? err.message : String(err)
					}`,
				),
			);
	};

	// Keep obj as the source of truth; sync yaml when entering/leaving YAML mode.
	const setModeSynced = (next: EditorMode) => {
		if (next === "yaml") {
			setYamlDraft(dump(obj));
		} else if (mode === "yaml") {
			try {
				setObj((load(yamlDraft) as Record<string, unknown>) ?? {});
			} catch {
				setNotice("YAML is invalid — fix it before switching views.");
				return;
			}
		}
		setNotice(null);
		setMode(next);
	};

	const setLayer = (key: string, value: Record<string, unknown>) =>
		setObj((prev) => ({ ...prev, [key]: value }));

	const toggleLayer = (key: OptionalLayerKey, on: boolean) => {
		setObj((prev) => {
			const next = { ...prev };
			if (on) next[key] = LAYER_DEFAULTS[key];
			else delete next[key];
			return next;
		});
		setSelectedLayer(on ? key : null);
	};

	const handleSave = async () => {
		const source = mode === "yaml" ? safeParse(yamlDraft) : obj;
		if (!source) {
			setNotice("YAML is invalid — cannot save.");
			return;
		}
		const meta = (source.metadata as Record<string, unknown> | undefined) ?? {};
		const id = (selectedId ?? (meta.id as string | undefined)) || "";
		if (!id) {
			setNotice("Funnel metadata.id is required to save.");
			return;
		}
		setSaving(true);
		setNotice(null);
		try {
			const result = await api.saveFunnel(id, dump(source));
			if (result.valid) {
				await api.reloadWorkspace().catch(() => undefined);
				setObj(source);
				setSavedObj(source);
				setSelectedId(id);
				loadList();
				setNotice("Saved.");
			} else {
				setNotice(
					result.errors?.map((e) => e.message).join(", ") ??
						"The runtime rejected the funnel.",
				);
			}
		} catch (err) {
			// The most likely cause is that the funnel CRUD route is not wired in this runtime yet.
			setNotice(
				`The funnel is schema-valid, but the runtime did not accept the write (PUT /api/funnels/${id}). The funnel CRUD routes may not be wired yet: ${
					err instanceof Error ? err.message : String(err)
				}`,
			);
		} finally {
			setSaving(false);
		}
	};

	const hasFunnelOpen = savedObj !== null;

	return (
		<div className="-m-6 flex h-[calc(100vh-3rem)] flex-col">
			{/* Header */}
			<div className="flex shrink-0 items-center gap-3 border-b px-4 py-2">
				<Funnel size={18} className="text-sky-500" />
				<span className="font-semibold">Funnels</span>
				<Button type="button" size="sm" onClick={() => setShowNew(true)}>
					<Plus size={12} /> New
				</Button>
				{hasFunnelOpen && (
					<>
						<span className="text-xs text-muted-foreground">{selectedId}</span>
						{dirty && <Badge variant="warning">unsaved</Badge>}
					</>
				)}
				{hasFunnelOpen && (
					<div className="ml-auto flex overflow-hidden rounded-md border">
						{(["canvas", "form", "yaml"] as const).map((m) => (
							<button
								key={m}
								type="button"
								onClick={() => setModeSynced(m)}
								className={cn(
									"px-3 py-1 text-xs capitalize transition-colors",
									mode === m
										? "bg-accent font-medium text-accent-foreground"
										: "text-muted-foreground hover:bg-accent/50",
								)}
							>
								{m}
							</button>
						))}
					</div>
				)}
				{hasFunnelOpen && (
					<Button
						type="button"
						size="sm"
						onClick={handleSave}
						disabled={saving}
					>
						{saving ? "Saving…" : "Save"}
					</Button>
				)}
			</div>

			{notice && (
				<div className="shrink-0 border-b bg-muted px-4 py-1.5 text-xs text-muted-foreground">
					{notice}
				</div>
			)}

			<div className="flex flex-1 overflow-hidden">
				{/* Left: funnel list */}
				<div className="w-56 shrink-0 overflow-y-auto border-r p-2">
					{listUnavailable && (
						<p className="px-2 py-2 text-xs text-muted-foreground">
							The runtime does not serve <code>/funnels</code> yet. You can
							still author and validate a new funnel here.
						</p>
					)}
					{!listUnavailable && names.length === 0 && (
						<p className="px-2 py-2 text-xs text-muted-foreground">
							No funnels yet. Create one with New.
						</p>
					)}
					{names.map((name) => (
						<button
							key={name}
							type="button"
							onClick={() => loadFunnel(name)}
							className={cn(
								"flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
								selectedId === name
									? "bg-accent font-medium text-accent-foreground"
									: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
							)}
						>
							<Funnel size={13} />
							{name}
						</button>
					))}
				</div>

				{/* Right: editor */}
				<div className="flex flex-1 flex-col overflow-hidden">
					{!hasFunnelOpen && (
						<div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
							Select a funnel, or create one with New.
						</div>
					)}

					{hasFunnelOpen && mode === "canvas" && (
						<div className="flex flex-1 flex-col overflow-hidden">
							<div className="relative min-h-[240px] flex-1 border-b">
								<FunnelCanvas
									funnel={obj}
									selectedLayer={selectedLayer}
									onSelectLayer={setSelectedLayer}
									editable
									onToggleLayer={toggleLayer}
								/>
							</div>
							<div className="flex h-[42%] shrink-0 flex-col overflow-hidden">
								<div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs">
									<Button
										type="button"
										variant={selectedLayer === null ? "secondary" : "outline"}
										size="sm"
										onClick={() => setSelectedLayer(null)}
									>
										Funnel details
									</Button>
									<span className="text-muted-foreground">
										click a layer to configure it · toggle optional layers on
										the cards
									</span>
								</div>
								<div className="flex-1 overflow-y-auto">
									<LayerConfig
										schema={schema}
										selectedLayer={selectedLayer}
										obj={obj}
										setLayer={setLayer}
										options={refOptions}
									/>
								</div>
							</div>
						</div>
					)}

					{hasFunnelOpen && mode === "form" && (
						<div className="flex-1 overflow-y-auto p-4">
							{schema ? (
								<SchemaForm
									schema={schema}
									value={obj}
									onChange={setObj}
									options={refOptions}
								/>
							) : (
								<p className="text-sm text-muted-foreground">
									Loading funnel schema…
								</p>
							)}
						</div>
					)}

					{hasFunnelOpen && mode === "yaml" && (
						<Textarea
							className="flex-1 resize-none rounded-none border-0 font-mono text-xs focus-visible:ring-0"
							value={yamlDraft}
							onChange={(e) => setYamlDraft(e.target.value)}
							spellCheck={false}
						/>
					)}
				</div>
			</div>

			{showNew && (
				<NewFunnelDialog
					onClose={() => setShowNew(false)}
					onCreate={(id, next) => {
						setShowNew(false);
						setMode("canvas");
						openFunnel(id, next);
					}}
				/>
			)}
		</div>
	);
}

function safeParse(text: string): Record<string, unknown> | null {
	try {
		return (load(text) as Record<string, unknown>) ?? {};
	} catch {
		return null;
	}
}
