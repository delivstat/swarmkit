"use client";

import { dump, load } from "js-yaml";
import {
	ChevronDown,
	ChevronRight,
	Crown,
	Layers,
	Plus,
	Shield,
	User,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { SchemaForm } from "@/components/schema-form";
import { TopologyCanvas } from "@/components/topology-canvas";
import { TopologyPalette } from "@/components/topology-palette";
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
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { JsonSchema } from "@/lib/schema-form";
import {
	type RawAgent,
	addChild,
	addSkill,
	removeAgent,
	reparent,
} from "@/lib/topology-edit";
import type { ResolvedAgent, TopologyDetail } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useRefOptions } from "@/lib/use-ref-options";
import { cn } from "@/lib/utils";

// Role → accent color (as a text-color class) + icon. Borders/dots/icons pick it up via currentColor,
// so a dynamic per-role accent needs no inline style.
const ROLE_STYLES: Record<string, { color: string; icon: typeof Crown }> = {
	root: { color: "text-sky-500", icon: Crown },
	leader: { color: "text-warning", icon: Shield },
	worker: { color: "text-success", icon: User },
};

function roleStyle(role: string) {
	return ROLE_STYLES[role] ?? ROLE_STYLES.worker;
}

function AgentNode({
	agent,
	depth,
	selectedId,
	onSelect,
}: {
	agent: ResolvedAgent;
	depth: number;
	selectedId: string | null;
	onSelect: (id: string) => void;
}) {
	const [expanded, setExpanded] = useState(true);
	const hasChildren = agent.children && agent.children.length > 0;
	const style = roleStyle(agent.role);
	const Icon = style?.icon ?? User;
	const isSelected = selectedId === agent.id;

	return (
		<div>
			<button
				type="button"
				onClick={() => onSelect(agent.id)}
				style={{ paddingLeft: `${depth * 20 + 12}px` }}
				className={cn(
					"flex w-full items-center gap-2 rounded-md py-2 pr-3 text-left text-sm transition-colors",
					isSelected
						? "bg-accent font-medium text-accent-foreground"
						: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
				)}
			>
				{hasChildren ? (
					<button
						type="button"
						onClick={(e) => {
							e.stopPropagation();
							setExpanded(!expanded);
						}}
						className="-ml-1 p-0.5"
					>
						{expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
					</button>
				) : (
					<span className="w-4" />
				)}
				<Icon size={14} className={cn("shrink-0", style?.color)} />
				<span className="text-foreground">{agent.id}</span>
				{agent.source_archetype && (
					<Badge variant="secondary" className="font-normal">
						{agent.source_archetype}
					</Badge>
				)}
				<span className="ml-auto text-xs text-muted-foreground">
					{agent.skills.length > 0 && `${agent.skills.length} skills`}
				</span>
			</button>
			{hasChildren && expanded && (
				<div>
					{agent.children?.map((child) => (
						<AgentNode
							key={child.id}
							agent={child}
							depth={depth + 1}
							selectedId={selectedId}
							onSelect={onSelect}
						/>
					))}
				</div>
			)}
		</div>
	);
}

function PropertyPanel({
	agent,
	yaml,
	onSave,
	saving,
	validationResult,
}: {
	agent: ResolvedAgent;
	yaml: string | null;
	onSave: (yaml: string) => void;
	saving: boolean;
	validationResult: { valid: boolean; errors?: { message: string }[] } | null;
}) {
	const style = roleStyle(agent.role);
	const [editingYaml, setEditingYaml] = useState(false);
	const [yamlContent, setYamlContent] = useState(yaml ?? "");

	useEffect(() => {
		if (yaml) setYamlContent(yaml);
	}, [yaml]);

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between">
				<div>
					<h3 className="text-lg font-semibold">{agent.id}</h3>
					<div className="mt-1 flex items-center gap-2">
						<Badge
							variant="outline"
							className={cn("border-current", style?.color)}
						>
							{agent.role}
						</Badge>
						{agent.source_archetype && (
							<span className="text-xs text-muted-foreground">
								archetype: {agent.source_archetype}
							</span>
						)}
					</div>
				</div>
				<div className="flex gap-2">
					<Button
						type="button"
						variant="outline"
						size="sm"
						onClick={() => setEditingYaml(!editingYaml)}
					>
						{editingYaml ? "View" : "YAML"}
					</Button>
					{editingYaml && (
						<Button
							type="button"
							size="sm"
							onClick={() => onSave(yamlContent)}
							disabled={saving}
						>
							{saving ? "Saving…" : "Save"}
						</Button>
					)}
				</div>
			</div>

			{validationResult && !validationResult.valid && (
				<div className="rounded-md border border-destructive p-2 text-xs text-destructive">
					{validationResult.errors?.map((e, i) => (
						<div key={`err-${e.message.slice(0, 20)}-${i}`}>{e.message}</div>
					))}
				</div>
			)}

			{editingYaml ? (
				<Textarea
					className="min-h-[400px] font-mono text-xs"
					value={yamlContent}
					onChange={(e) => setYamlContent(e.target.value)}
					spellCheck={false}
				/>
			) : (
				<>
					{agent.model && (
						<Card>
							<CardTitle>Model</CardTitle>
							<div className="space-y-1 text-sm">
								{Object.entries(agent.model).map(([k, v]) => (
									<div key={k} className="flex justify-between">
										<span className="text-muted-foreground">{k}</span>
										<span className="font-mono text-xs">{String(v)}</span>
									</div>
								))}
							</div>
						</Card>
					)}

					<Card>
						<CardTitle>Skills ({agent.skills.length})</CardTitle>
						{agent.skills.length === 0 ? (
							<p className="text-sm text-muted-foreground">
								No skills assigned
							</p>
						) : (
							<div className="flex flex-wrap gap-1.5">
								{agent.skills.map((s) => (
									<Badge key={s} variant="secondary" className="font-normal">
										{s}
									</Badge>
								))}
							</div>
						)}
					</Card>

					{agent.children && agent.children.length > 0 && (
						<Card>
							<CardTitle>Children ({agent.children.length})</CardTitle>
							<div className="space-y-1">
								{agent.children.map((c) => {
									const cs = roleStyle(c.role);
									return (
										<div key={c.id} className="flex items-center gap-2 text-sm">
											<span
												className={cn(
													"size-2 rounded-full bg-current",
													cs?.color,
												)}
											/>
											<span>{c.id}</span>
											<span className="text-xs text-muted-foreground">
												{c.role}
											</span>
										</div>
									);
								})}
							</div>
						</Card>
					)}
				</>
			)}
		</div>
	);
}

function RelationshipsView({
	agent,
	root,
	onSelect,
}: {
	agent: ResolvedAgent;
	root: ResolvedAgent;
	onSelect: (id: string) => void;
}) {
	const style = roleStyle(agent.role);
	const Icon = style?.icon ?? User;
	const parent = findParent(root, agent.id);

	return (
		<div className="flex min-h-full flex-col items-center gap-6 p-4">
			{/* Parent */}
			{parent && (
				<>
					<button
						type="button"
						onClick={() => onSelect(parent.id)}
						className="flex items-center gap-2 rounded-lg border bg-card px-4 py-2 text-sm hover:bg-accent/50"
					>
						<span className="text-xs text-muted-foreground">parent</span>
						<span className="font-medium">{parent.id}</span>
					</button>
					<div className="h-4 w-px bg-border" />
				</>
			)}

			{/* Selected agent — center */}
			<div
				className={cn(
					"rounded-xl border-2 border-current bg-card px-6 py-4 text-center",
					style?.color,
				)}
			>
				<Icon size={24} className="mx-auto mb-1" />
				<div className="text-lg font-semibold text-foreground">{agent.id}</div>
				<div className="text-xs text-muted-foreground">
					{agent.role}
					{agent.source_archetype && ` · ${agent.source_archetype}`}
				</div>
				{agent.model && (
					<div className="mt-1 font-mono text-xs text-muted-foreground">
						{((agent.model as Record<string, unknown>).name as string) ?? ""}
					</div>
				)}
			</div>

			{/* Connections row */}
			<div className="flex items-start gap-8">
				{/* Skills (left) */}
				<div className="space-y-2">
					<div className="text-center text-xs font-semibold text-muted-foreground">
						Skills
					</div>
					{agent.skills.length === 0 ? (
						<div className="text-center text-xs text-muted-foreground">
							none
						</div>
					) : (
						agent.skills.map((s) => (
							<div
								key={s}
								className="rounded-md border bg-background px-3 py-1.5 text-xs"
							>
								{s}
							</div>
						))
					)}
				</div>

				{/* Children (center) */}
				{agent.children && agent.children.length > 0 && (
					<div className="space-y-2">
						<div className="text-center text-xs font-semibold text-muted-foreground">
							Children
						</div>
						{agent.children.map((c) => {
							const cs = roleStyle(c.role);
							return (
								<button
									key={c.id}
									type="button"
									onClick={() => onSelect(c.id)}
									className="flex w-full items-center gap-2 rounded-md border bg-background px-3 py-1.5 text-xs hover:bg-accent/50"
								>
									<span
										className={cn("size-2 rounded-full bg-current", cs?.color)}
									/>
									<span>{c.id}</span>
									<span className="text-muted-foreground">{c.role}</span>
								</button>
							);
						})}
					</div>
				)}
			</div>
		</div>
	);
}

function NetworkView({
	root,
	selectedId,
	onSelect,
}: {
	root: ResolvedAgent;
	selectedId: string | null;
	onSelect: (id: string) => void;
}) {
	const allAgents = flattenAgents(root);

	return (
		<div className="flex flex-wrap content-start gap-3 p-6">
			{allAgents.map((agent) => {
				const style = roleStyle(agent.role);
				const Icon = style?.icon ?? User;
				const isSelected = selectedId === agent.id;
				const parent = findParent(root, agent.id);

				return (
					<button
						key={agent.id}
						type="button"
						onClick={() => onSelect(agent.id)}
						className={cn(
							"flex flex-col items-center gap-1 rounded-lg border-2 bg-card px-4 py-3 text-xs transition-all",
							style?.color,
							isSelected
								? "border-current"
								: "border-border hover:bg-accent/50",
						)}
					>
						<Icon size={18} />
						<span className="font-medium text-foreground">{agent.id}</span>
						<span className="text-muted-foreground">
							{agent.skills.length} skills
						</span>
						{parent && (
							<span className="text-muted-foreground">← {parent.id}</span>
						)}
					</button>
				);
			})}

			{/* Edges */}
			<div className="mt-4 w-full border-t pt-4">
				<div className="mb-2 text-xs font-semibold text-muted-foreground">
					Delegation paths
				</div>
				{allAgents
					.filter((a) => a.children && a.children.length > 0)
					.map((a) => (
						<div key={a.id} className="mb-1 text-xs text-muted-foreground">
							{a.id} → {a.children?.map((c) => c.id).join(", ")}
						</div>
					))}
			</div>
		</div>
	);
}

function flattenAgents(agent: ResolvedAgent): ResolvedAgent[] {
	const result: ResolvedAgent[] = [agent];
	for (const child of agent.children ?? []) {
		result.push(...flattenAgents(child));
	}
	return result;
}

function findParent(
	root: ResolvedAgent,
	childId: string,
): ResolvedAgent | null {
	for (const child of root.children ?? []) {
		if (child.id === childId) return root;
		const found = findParent(child, childId);
		if (found) return found;
	}
	return null;
}

function findAgent(root: ResolvedAgent, id: string): ResolvedAgent | null {
	if (root.id === id) return root;
	for (const child of root.children ?? []) {
		const found = findAgent(child, id);
		if (found) return found;
	}
	return null;
}

const NEW_TOPOLOGY_TEMPLATE = `apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: NAME
  version: "1.0.0"
  description: ""
agents:
  root:
    id: root
    role: root
    model:
      provider: openrouter
      name: moonshotai/kimi-k2.6
    prompt:
      system: |
        You are the root coordinator.
    children: []
`;

function NewTopologyDialog({
	onClose,
	onCreate,
}: {
	onClose: () => void;
	onCreate: (name: string) => void;
}) {
	const [name, setName] = useState("");
	const [creating, setCreating] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const handleCreate = async () => {
		const slug = name
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-|-$/g, "");
		if (!slug) {
			setError("Name must contain at least one letter");
			return;
		}
		setCreating(true);
		setError(null);
		try {
			const yaml = NEW_TOPOLOGY_TEMPLATE.replace("NAME", slug);
			const result = await api.saveTopology(slug, yaml);
			if (result.valid) {
				await api.reloadWorkspace();
				onCreate(slug);
			} else {
				setError(
					result.errors?.map((e) => e.message).join(", ") ??
						"Validation failed",
				);
			}
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setCreating(false);
		}
	};

	return (
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>New Topology</DialogTitle>
				</DialogHeader>
				<div className="space-y-1.5">
					<Label htmlFor="topo-name">Name (kebab-case)</Label>
					<Input
						id="topo-name"
						placeholder="my-topology"
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
					<Button
						type="button"
						onClick={handleCreate}
						disabled={creating || !name.trim()}
					>
						{creating ? "Creating…" : "Create"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

function countAgents(agent: ResolvedAgent): number {
	let count = 1;
	for (const child of agent.children ?? []) {
		count += countAgents(child);
	}
	return count;
}

/** Schema-driven full-topology editor (agents array with archetype/skills ref pickers + tooltips),
 * a peer to the visual structure/relationships/network views. */
function TopologyFormView({
	schema,
	value,
	onChange,
	options,
	onSave,
	saving,
}: {
	schema: JsonSchema | null;
	value: Record<string, unknown>;
	onChange: (v: Record<string, unknown>) => void;
	options: Record<string, string[]>;
	onSave: () => void;
	saving: boolean;
}) {
	if (!schema) {
		return (
			<div className="p-4 text-sm text-muted-foreground">Loading schema…</div>
		);
	}
	return (
		<div className="flex-1 overflow-y-auto p-4">
			<div className="mb-3 flex justify-end">
				<Button type="button" size="sm" onClick={onSave} disabled={saving}>
					{saving ? "Saving…" : "Save"}
				</Button>
			</div>
			<SchemaForm
				schema={schema}
				value={value}
				onChange={onChange}
				options={options}
			/>
		</div>
	);
}

const VIEWS = [
	"structure",
	"relationships",
	"network",
	"canvas",
	"form",
] as const;
type View = (typeof VIEWS)[number];

export default function ComposerPage() {
	const searchParams = useSearchParams();
	const fetchTopologies = useCallback(() => api.topologies(), []);
	const { data: topologyNames } = usePoll<string[]>(fetchTopologies, 30000);

	const [selectedTopology, setSelectedTopology] = useState<string | null>(null);
	const [autoLoaded, setAutoLoaded] = useState(false);
	const [showNewDialog, setShowNewDialog] = useState(false);
	const [topologyDetail, setTopologyDetail] = useState<TopologyDetail | null>(
		null,
	);
	const [topologyYaml, setTopologyYaml] = useState<string | null>(null);
	const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
	const [activeView, setActiveView] = useState<View>("structure");
	const [canvasEditing, setCanvasEditing] = useState(false);
	const [topologySchema, setTopologySchema] = useState<JsonSchema | null>(null);
	const [formObj, setFormObj] = useState<Record<string, unknown>>({});
	const refOptions = useRefOptions();
	const [loading, setLoading] = useState(false);
	const [saving, setSaving] = useState(false);
	const [yamlPanelOpen, setYamlPanelOpen] = useState(false);
	const [yamlDraft, setYamlDraft] = useState("");
	const [validationResult, setValidationResult] = useState<{
		valid: boolean;
		errors?: { message: string }[];
	} | null>(null);

	useEffect(() => {
		const t = searchParams.get("topology");
		if (t && !autoLoaded) {
			setAutoLoaded(true);
			loadTopology(t);
		}
	}, [searchParams, autoLoaded]);

	useEffect(() => {
		api
			.schema("topology")
			.then((s) => setTopologySchema(s as JsonSchema))
			.catch(() => setTopologySchema(null));
	}, []);

	const loadTopology = async (id: string) => {
		setSelectedTopology(id);
		setLoading(true);
		setValidationResult(null);
		try {
			const [detail, yamlResp] = await Promise.all([
				api.topologyDetail(id),
				api.topologyYaml(id),
			]);
			setTopologyDetail(detail);
			setTopologyYaml(yamlResp.yaml);
			setYamlDraft(yamlResp.yaml);
			try {
				setFormObj((load(yamlResp.yaml) as Record<string, unknown>) ?? {});
			} catch {
				setFormObj({});
			}
			setSelectedAgentId(detail.resolved.id);
		} catch {
			setTopologyDetail(null);
			setTopologyYaml(null);
		} finally {
			setLoading(false);
		}
	};

	const handleSave = async (yaml: string) => {
		if (!selectedTopology) return;
		setSaving(true);
		setValidationResult(null);
		try {
			const result = await api.saveTopology(selectedTopology, yaml);
			setValidationResult(result);
			if (result.valid) {
				setTopologyYaml(yaml);
				await loadTopology(selectedTopology);
			}
		} catch (err) {
			setValidationResult({
				valid: false,
				errors: [{ message: err instanceof Error ? err.message : String(err) }],
			});
		} finally {
			setSaving(false);
		}
	};

	// Canvas edit: apply a pure structural op to the raw `agents.root` tree and round-trip through
	// YAML (invariant #1 — no second source of truth). A no-op op (returns the same tree) is skipped.
	const applyCanvasEdit = (fn: (root: RawAgent) => RawAgent) => {
		if (!topologyYaml) return;
		let obj: Record<string, unknown>;
		try {
			obj = (load(topologyYaml) as Record<string, unknown>) ?? {};
		} catch {
			return;
		}
		const agents = obj.agents as { root?: RawAgent } | undefined;
		const root = agents?.root;
		if (!root) return;
		const next = fn(root);
		if (next === root) return;
		obj.agents = { ...agents, root: next };
		handleSave(dump(obj));
	};

	// Add an agent under `targetId` (a node dropped onto), else the selected agent, else the root.
	// A palette archetype instantiates that archetype (raw `archetype:` key); otherwise a blank worker.
	const addCanvasAgent = (targetId?: string | null, archetypeId?: string) => {
		if (!topologyDetail) return;
		const existing = new Set(
			flattenAgents(topologyDetail.resolved).map((a) => a.id),
		);
		const base = archetypeId ?? "agent";
		let n = existing.size + 1;
		while (existing.has(`${base}-${n}`)) n += 1;
		const id = `${base}-${n}`;
		const parentId = targetId ?? selectedAgentId ?? topologyDetail.resolved.id;
		const child: RawAgent = archetypeId
			? { id, archetype: archetypeId }
			: { id, role: "worker" };
		applyCanvasEdit((root) => addChild(root, parentId, child));
	};

	const addCanvasSkill = (agentId: string, skillId: string) =>
		applyCanvasEdit((root) => addSkill(root, agentId, skillId));

	const selectedAgent =
		topologyDetail && selectedAgentId
			? findAgent(topologyDetail.resolved, selectedAgentId)
			: null;

	const showsTree =
		activeView === "structure" ||
		activeView === "relationships" ||
		activeView === "network";

	return (
		<div className="-m-6 flex h-[calc(100vh-3rem)] flex-col">
			{/* Header */}
			<div className="flex shrink-0 items-center gap-3 border-b px-4 py-2">
				<Layers size={18} className="text-sky-500" />
				<span className="font-semibold">Composer</span>

				<Select
					value={selectedTopology ?? undefined}
					onValueChange={(v) => loadTopology(v)}
				>
					<SelectTrigger className="h-8 w-52">
						<SelectValue placeholder="Select topology…" />
					</SelectTrigger>
					<SelectContent>
						{topologyNames?.map((name) => (
							<SelectItem key={name} value={name}>
								{name}
							</SelectItem>
						))}
					</SelectContent>
				</Select>

				<Button type="button" size="sm" onClick={() => setShowNewDialog(true)}>
					<Plus size={12} /> New
				</Button>

				{topologyDetail && (
					<>
						<Badge variant="secondary">v{topologyDetail.version}</Badge>
						<span className="text-xs text-muted-foreground">
							{countAgents(topologyDetail.resolved)} agents
						</span>
					</>
				)}

				{topologyDetail && (
					<Button
						type="button"
						variant={yamlPanelOpen ? "secondary" : "outline"}
						size="sm"
						onClick={() => setYamlPanelOpen(!yamlPanelOpen)}
					>
						{yamlPanelOpen ? "Hide YAML" : "YAML"}
					</Button>
				)}

				<div className="ml-auto flex overflow-hidden rounded-md border">
					{VIEWS.map((view) => (
						<button
							key={view}
							type="button"
							onClick={() => setActiveView(view)}
							className={cn(
								"px-3 py-1 text-xs capitalize transition-colors",
								activeView === view
									? "bg-accent font-medium text-accent-foreground"
									: "text-muted-foreground hover:bg-accent/50",
							)}
						>
							{view}
						</button>
					))}
				</div>
			</div>

			{/* Content */}
			<div className="flex flex-1 overflow-hidden">
				{/* Left: Agent Tree */}
				{showsTree && (
					<div className="w-72 shrink-0 overflow-y-auto border-r">
						{!topologyDetail && !loading && (
							<div className="p-4 text-center text-sm text-muted-foreground">
								Select a topology to begin editing
							</div>
						)}
						{loading && (
							<div className="p-4 text-center text-sm text-muted-foreground">
								Loading…
							</div>
						)}
						{topologyDetail && (
							<div className="py-2">
								<AgentNode
									agent={topologyDetail.resolved}
									depth={0}
									selectedId={selectedAgentId}
									onSelect={setSelectedAgentId}
								/>
							</div>
						)}
					</div>
				)}

				{/* Right: View content */}
				<div className="flex-1 overflow-y-auto">
					{activeView === "form" && (
						<TopologyFormView
							schema={topologySchema}
							value={formObj}
							onChange={setFormObj}
							options={refOptions}
							onSave={() => handleSave(dump(formObj))}
							saving={saving}
						/>
					)}
					{activeView === "structure" && (
						<div className="p-4">
							{!selectedAgent && topologyDetail && (
								<div className="py-12 text-center text-sm text-muted-foreground">
									Select an agent from the tree
								</div>
							)}
							{selectedAgent && (
								<PropertyPanel
									agent={selectedAgent}
									yaml={topologyYaml}
									onSave={handleSave}
									saving={saving}
									validationResult={validationResult}
								/>
							)}
						</div>
					)}
					{activeView === "relationships" &&
						topologyDetail &&
						selectedAgent && (
							<RelationshipsView
								agent={selectedAgent}
								root={topologyDetail.resolved}
								onSelect={setSelectedAgentId}
							/>
						)}
					{activeView === "relationships" &&
						topologyDetail &&
						!selectedAgent && (
							<div className="p-4 py-12 text-center text-sm text-muted-foreground">
								Select an agent to view relationships
							</div>
						)}
					{activeView === "network" && topologyDetail && (
						<NetworkView
							root={topologyDetail.resolved}
							selectedId={selectedAgentId}
							onSelect={setSelectedAgentId}
						/>
					)}
					{activeView === "canvas" && topologyDetail && (
						<div className="flex h-full flex-col">
							<div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs">
								<Button
									type="button"
									variant={canvasEditing ? "secondary" : "outline"}
									size="sm"
									onClick={() => setCanvasEditing((v) => !v)}
								>
									{canvasEditing ? "Editing" : "Edit"}
								</Button>
								{canvasEditing && (
									<>
										<Button
											type="button"
											variant="outline"
											size="sm"
											onClick={() => addCanvasAgent()}
											disabled={saving}
										>
											<Plus size={12} /> agent
											{selectedAgentId ? ` under ${selectedAgentId}` : ""}
										</Button>
										<span className="text-muted-foreground">
											click the palette to add · drag between nodes to delegate
											· Delete removes a node{saving ? " · saving…" : ""}
										</span>
										{validationResult && !validationResult.valid && (
											<span className="ml-auto truncate text-destructive">
												save rejected:{" "}
												{validationResult.errors?.[0]?.message ?? "invalid"}
											</span>
										)}
									</>
								)}
							</div>
							<div className="flex flex-1 overflow-hidden">
								{canvasEditing && (
									<TopologyPalette
										archetypes={refOptions.archetype ?? []}
										skills={refOptions.skill ?? []}
										selectedId={selectedAgentId}
										onAddAgent={(archetypeId) =>
											addCanvasAgent(null, archetypeId)
										}
										onAddSkill={(skillId) =>
											addCanvasSkill(
												selectedAgentId ?? topologyDetail.resolved.id,
												skillId,
											)
										}
									/>
								)}
								<div className="flex-1">
									<TopologyCanvas
										root={topologyDetail.resolved}
										onSelect={setSelectedAgentId}
										editable={canvasEditing}
										onConnect={(source, target) =>
											applyCanvasEdit((root) => reparent(root, target, source))
										}
										onDeleteNode={(id) =>
											applyCanvasEdit((root) => removeAgent(root, id))
										}
										onAddChild={addCanvasAgent}
										onAddSkill={addCanvasSkill}
									/>
								</div>
							</div>
						</div>
					)}
				</div>
			</div>

			{/* Bottom: YAML Panel */}
			{yamlPanelOpen && topologyDetail && (
				<div className="h-[280px] shrink-0 border-t">
					<div className="flex items-center justify-between border-b bg-card px-3 py-1.5">
						<span className="text-xs font-medium">
							Topology YAML — {selectedTopology}
						</span>
						<div className="flex items-center gap-2">
							{yamlDraft !== topologyYaml && (
								<Badge variant="warning">unsaved</Badge>
							)}
							<Button
								type="button"
								size="sm"
								onClick={() => handleSave(yamlDraft)}
								disabled={saving || yamlDraft === topologyYaml}
							>
								{saving ? "Saving…" : "Save"}
							</Button>
						</div>
					</div>
					<Textarea
						className="h-[calc(100%-40px)] resize-none rounded-none border-0 font-mono text-xs focus-visible:ring-0"
						value={yamlDraft}
						onChange={(e) => setYamlDraft(e.target.value)}
						spellCheck={false}
					/>
				</div>
			)}

			{showNewDialog && (
				<NewTopologyDialog
					onClose={() => setShowNewDialog(false)}
					onCreate={(name) => {
						setShowNewDialog(false);
						loadTopology(name);
					}}
				/>
			)}
		</div>
	);
}
