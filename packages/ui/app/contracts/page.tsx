"use client";

// The Contracts surface (design/details/contract-registry.md): author/inspect Contract artifacts —
// integration contracts, the agreed interface between two (or more) apps. A contract is the checked,
// pickable vocabulary a pipeline stage's `locks` reference (instead of typo-prone free strings): the
// contract makes a lock id real and carries which apps it binds (`parties`). It is not executed.
// Mirrors how funnels/pipelines are surfaced (list + form/yaml editor) — there is no canvas, a
// contract is a flat artifact.
//
// Defensive by design: the runtime serves the contract schema (GET /api/schema/contract) and
// `@swarmkit/schema` validates locally, but the contract CRUD routes (/contracts, /api/contracts/{id})
// may not be wired yet — the list gates gracefully to empty and a save the runtime rejects surfaces a
// clear "not persisted" notice rather than failing silently.

import { dump, load } from "js-yaml";
import { Handshake, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

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
import { readContract } from "@/lib/contract";
import type { JsonSchema } from "@/lib/schema-form";
import { useRefOptions } from "@/lib/use-ref-options";
import { cn } from "@/lib/utils";

const NEW_CONTRACT_TEMPLATE = `apiVersion: swarmkit/v1
kind: Contract
metadata:
  id: NAME
  name: ""
  description: "Integration contract between apps (min 10 chars)."
parties:
  - ""
  - ""
interface: ""
provenance:
  authored_by: human
  version: 1.0.0
`;

type EditorMode = "form" | "yaml";

function NewContractDialog({
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
			const obj = load(NEW_CONTRACT_TEMPLATE.replace("NAME", slug)) as Record<
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
					<DialogTitle>New Contract</DialogTitle>
				</DialogHeader>
				<div className="space-y-1.5">
					<Label htmlFor="contract-name">Contract ID (kebab-case)</Label>
					<Input
						id="contract-name"
						placeholder="oms-web"
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

export default function ContractsPage() {
	const [schema, setSchema] = useState<JsonSchema | null>(null);
	const [names, setNames] = useState<string[]>([]);
	const [listUnavailable, setListUnavailable] = useState(false);
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [obj, setObj] = useState<Record<string, unknown>>({});
	const [savedObj, setSavedObj] = useState<Record<string, unknown> | null>(
		null,
	);
	const [yamlDraft, setYamlDraft] = useState("");
	const [mode, setMode] = useState<EditorMode>("form");
	const [showNew, setShowNew] = useState(false);
	const [saving, setSaving] = useState(false);
	const [notice, setNotice] = useState<string | null>(null);
	const refOptions = useRefOptions();

	// The contract schema drives the form. The runtime serves it at GET /api/schema/contract
	// (SchemaName includes "contract"); a failure leaves the form gated.
	useEffect(() => {
		api
			.schema("contract")
			.then((s) => setSchema(s as JsonSchema))
			.catch(() => setSchema(null));
	}, []);

	const loadList = useCallback(() => {
		api
			.contracts()
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

	// A live projection of the open contract for the header summary (parties it binds).
	const spec = useMemo(() => readContract(obj), [obj]);

	const openContract = (id: string, next: Record<string, unknown>) => {
		setSelectedId(id);
		setObj(next);
		setSavedObj(next);
		setYamlDraft(dump(next));
		setNotice(null);
	};

	const loadContract = (id: string) => {
		api
			.contractYaml(id)
			.then((r) => {
				const parsed = (load(r.yaml) as Record<string, unknown>) ?? {};
				openContract(id, parsed);
			})
			.catch((err) =>
				setNotice(
					`Could not load contract "${id}" (the runtime may not serve /api/contracts yet): ${
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

	const handleSave = async () => {
		const source = mode === "yaml" ? safeParse(yamlDraft) : obj;
		if (!source) {
			setNotice("YAML is invalid — cannot save.");
			return;
		}
		const meta = (source.metadata as Record<string, unknown> | undefined) ?? {};
		const id = (selectedId ?? (meta.id as string | undefined)) || "";
		if (!id) {
			setNotice("Contract metadata.id is required to save.");
			return;
		}
		setSaving(true);
		setNotice(null);
		try {
			const result = await api.saveContract(id, dump(source));
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
						"The runtime rejected the contract.",
				);
			}
		} catch (err) {
			// The most likely cause is that the contract CRUD route is not wired in this runtime yet.
			setNotice(
				`The contract is schema-valid, but the runtime did not accept the write (PUT /api/contracts/${id}). The contract CRUD routes may not be wired yet: ${
					err instanceof Error ? err.message : String(err)
				}`,
			);
		} finally {
			setSaving(false);
		}
	};

	const hasContractOpen = savedObj !== null;

	return (
		<div className="-m-6 flex h-[calc(100vh-3rem)] flex-col">
			{/* Header */}
			<div className="flex shrink-0 items-center gap-3 border-b px-4 py-2">
				<Handshake size={18} className="text-sky-500" />
				<span className="font-semibold">Contracts</span>
				<Button type="button" size="sm" onClick={() => setShowNew(true)}>
					<Plus size={12} /> New
				</Button>
				{hasContractOpen && (
					<>
						<span className="text-xs text-muted-foreground">{selectedId}</span>
						{spec.parties.length >= 2 && (
							<Badge variant="secondary">
								{spec.parties.filter((p) => p).join(" ↔ ")}
							</Badge>
						)}
						{dirty && <Badge variant="warning">unsaved</Badge>}
					</>
				)}
				{hasContractOpen && (
					<div className="ml-auto flex overflow-hidden rounded-md border">
						{(["form", "yaml"] as const).map((m) => (
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
				{hasContractOpen && (
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
				{/* Left: contract list */}
				<div className="w-56 shrink-0 overflow-y-auto border-r p-2">
					{listUnavailable && (
						<p className="px-2 py-2 text-xs text-muted-foreground">
							The runtime does not serve <code>/contracts</code> yet. You can
							still author and validate a new contract here.
						</p>
					)}
					{!listUnavailable && names.length === 0 && (
						<p className="px-2 py-2 text-xs text-muted-foreground">
							No contracts yet. Create one with New.
						</p>
					)}
					{names.map((name) => (
						<button
							key={name}
							type="button"
							onClick={() => loadContract(name)}
							className={cn(
								"flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
								selectedId === name
									? "bg-accent font-medium text-accent-foreground"
									: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
							)}
						>
							<Handshake size={13} />
							{name}
						</button>
					))}
				</div>

				{/* Right: editor */}
				<div className="flex flex-1 flex-col overflow-hidden">
					{!hasContractOpen && (
						<div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
							Select a contract, or create one with New.
						</div>
					)}

					{hasContractOpen && mode === "form" && (
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
									Loading contract schema…
								</p>
							)}
						</div>
					)}

					{hasContractOpen && mode === "yaml" && (
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
				<NewContractDialog
					onClose={() => setShowNew(false)}
					onCreate={(id, next) => {
						setShowNew(false);
						setMode("form");
						openContract(id, next);
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
