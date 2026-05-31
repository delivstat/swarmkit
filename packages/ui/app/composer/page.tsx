"use client";

import { Card, CardTitle } from "@/components/card";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { ResolvedAgent, SkillItem, TopologyDetail } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import {
	ChevronDown,
	ChevronRight,
	Crown,
	Layers,
	Shield,
	User,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

const ROLE_STYLES: Record<string, { color: string; icon: typeof Crown }> = {
	root: { color: "var(--accent)", icon: Crown },
	leader: { color: "var(--warning)", icon: Shield },
	worker: { color: "var(--success)", icon: User },
};

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
	const style = ROLE_STYLES[agent.role] ?? ROLE_STYLES.worker;
	const Icon = style?.icon ?? User;
	const isSelected = selectedId === agent.id;

	return (
		<div>
			<button
				type="button"
				onClick={() => onSelect(agent.id)}
				className={cn(
					"flex items-center gap-2 w-full text-left px-3 py-2 rounded-md text-sm transition-colors",
					isSelected ? "font-medium" : "hover:opacity-80",
				)}
				style={{
					paddingLeft: `${depth * 20 + 12}px`,
					background: isSelected ? "var(--border)" : undefined,
				}}
			>
				{hasChildren ? (
					<button
						type="button"
						onClick={(e) => {
							e.stopPropagation();
							setExpanded(!expanded);
						}}
						className="p-0.5 -ml-1"
					>
						{expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
					</button>
				) : (
					<span className="w-4" />
				)}
				<Icon size={14} style={{ color: style?.color }} />
				<span>{agent.id}</span>
				{agent.source_archetype && (
					<span
						className="text-xs px-1.5 py-0.5 rounded"
						style={{
							background: "var(--bg)",
							color: "var(--fg-muted)",
						}}
					>
						{agent.source_archetype}
					</span>
				)}
				<span className="text-xs ml-auto" style={{ color: "var(--fg-muted)" }}>
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
	const style = ROLE_STYLES[agent.role] ?? ROLE_STYLES.worker;
	const [editingYaml, setEditingYaml] = useState(false);
	const [yamlContent, setYamlContent] = useState(yaml ?? "");

	useEffect(() => {
		if (yaml) setYamlContent(yaml);
	}, [yaml]);

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between">
				<div>
					<h3 className="text-lg font-bold">{agent.id}</h3>
					<div className="flex items-center gap-2 mt-1">
						<span
							className="text-xs px-2 py-0.5 rounded-full font-medium"
							style={{
								color: style?.color,
								border: `1px solid ${style?.color}40`,
							}}
						>
							{agent.role}
						</span>
						{agent.source_archetype && (
							<span className="text-xs" style={{ color: "var(--fg-muted)" }}>
								archetype: {agent.source_archetype}
							</span>
						)}
					</div>
				</div>
				<div className="flex gap-2">
					<button
						type="button"
						onClick={() => setEditingYaml(!editingYaml)}
						className="text-xs px-2.5 py-1 rounded"
						style={{ border: "1px solid var(--border)" }}
					>
						{editingYaml ? "View" : "YAML"}
					</button>
					{editingYaml && (
						<button
							type="button"
							onClick={() => onSave(yamlContent)}
							disabled={saving}
							className="text-xs px-2.5 py-1 rounded font-medium disabled:opacity-40"
							style={{
								background: "var(--accent)",
								color: "var(--accent-fg)",
							}}
						>
							{saving ? "Saving..." : "Save"}
						</button>
					)}
				</div>
			</div>

			{validationResult && !validationResult.valid && (
				<div
					className="text-xs p-2 rounded"
					style={{
						background: "var(--bg)",
						border: "1px solid var(--error)",
						color: "var(--error)",
					}}
				>
					{validationResult.errors?.map((e, i) => (
						<div key={`err-${e.message.slice(0, 20)}-${i}`}>{e.message}</div>
					))}
				</div>
			)}

			{editingYaml ? (
				<div>
					<textarea
						className="w-full font-mono text-xs p-3 rounded border resize-none"
						style={{
							background: "var(--bg)",
							borderColor: "var(--border)",
							color: "var(--fg)",
							minHeight: "400px",
						}}
						value={yamlContent}
						onChange={(e) => setYamlContent(e.target.value)}
						spellCheck={false}
					/>
				</div>
			) : (
				<>
					{agent.model && (
						<Card>
							<CardTitle>Model</CardTitle>
							<div className="text-sm space-y-1">
								{Object.entries(agent.model).map(([k, v]) => (
									<div key={k} className="flex justify-between">
										<span style={{ color: "var(--fg-muted)" }}>{k}</span>
										<span className="font-mono text-xs">{String(v)}</span>
									</div>
								))}
							</div>
						</Card>
					)}

					<Card>
						<CardTitle>Skills ({agent.skills.length})</CardTitle>
						{agent.skills.length === 0 ? (
							<p className="text-sm" style={{ color: "var(--fg-muted)" }}>
								No skills assigned
							</p>
						) : (
							<div className="flex flex-wrap gap-1.5">
								{agent.skills.map((s) => (
									<span
										key={s}
										className="text-xs px-2 py-1 rounded"
										style={{
											background: "var(--bg)",
											border: "1px solid var(--border)",
										}}
									>
										{s}
									</span>
								))}
							</div>
						)}
					</Card>

					{agent.children && agent.children.length > 0 && (
						<Card>
							<CardTitle>Children ({agent.children.length})</CardTitle>
							<div className="space-y-1">
								{agent.children.map((c) => {
									const cs = ROLE_STYLES[c.role] ?? ROLE_STYLES.worker;
									return (
										<div key={c.id} className="flex items-center gap-2 text-sm">
											<span
												className="w-2 h-2 rounded-full"
												style={{ background: cs?.color }}
											/>
											<span>{c.id}</span>
											<span
												className="text-xs"
												style={{ color: "var(--fg-muted)" }}
											>
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

function findAgent(root: ResolvedAgent, id: string): ResolvedAgent | null {
	if (root.id === id) return root;
	for (const child of root.children ?? []) {
		const found = findAgent(child, id);
		if (found) return found;
	}
	return null;
}

function countAgents(agent: ResolvedAgent): number {
	let count = 1;
	for (const child of agent.children ?? []) {
		count += countAgents(child);
	}
	return count;
}

export default function ComposerPage() {
	const searchParams = useSearchParams();
	const fetchTopologies = useCallback(() => api.topologies(), []);
	const { data: topologyNames } = usePoll<string[]>(fetchTopologies, 30000);

	const [selectedTopology, setSelectedTopology] = useState<string | null>(null);
	const [autoLoaded, setAutoLoaded] = useState(false);
	const [topologyDetail, setTopologyDetail] = useState<TopologyDetail | null>(
		null,
	);
	const [topologyYaml, setTopologyYaml] = useState<string | null>(null);
	const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
	const [activeView, setActiveView] = useState<
		"structure" | "relationships" | "network"
	>("structure");
	const [loading, setLoading] = useState(false);
	const [saving, setSaving] = useState(false);
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
				errors: [
					{
						message: err instanceof Error ? err.message : String(err),
					},
				],
			});
		} finally {
			setSaving(false);
		}
	};

	const selectedAgent =
		topologyDetail && selectedAgentId
			? findAgent(topologyDetail.resolved, selectedAgentId)
			: null;

	return (
		<div className="flex flex-col h-[calc(100vh-3rem)] -m-6">
			{/* Header */}
			<div
				className="flex items-center gap-4 px-4 py-2 border-b shrink-0"
				style={{ borderColor: "var(--border)" }}
			>
				<Layers size={18} style={{ color: "var(--accent)" }} />
				<span className="font-bold">Composer</span>

				<select
					className="px-2 py-1 rounded border text-sm"
					style={{
						background: "var(--bg)",
						borderColor: "var(--border)",
						color: "var(--fg)",
					}}
					value={selectedTopology ?? ""}
					onChange={(e) => {
						if (e.target.value) loadTopology(e.target.value);
					}}
				>
					<option value="">Select topology...</option>
					{topologyNames?.map((name) => (
						<option key={name} value={name}>
							{name}
						</option>
					))}
				</select>

				{topologyDetail && (
					<>
						<span
							className="text-xs px-2 py-0.5 rounded"
							style={{
								background: "var(--bg-sidebar)",
								color: "var(--fg-muted)",
							}}
						>
							v{topologyDetail.version}
						</span>
						<span className="text-xs" style={{ color: "var(--fg-muted)" }}>
							{countAgents(topologyDetail.resolved)} agents
						</span>
					</>
				)}

				<div
					className="flex ml-auto rounded-md overflow-hidden border"
					style={{ borderColor: "var(--border)" }}
				>
					{(["structure", "relationships", "network"] as const).map((view) => (
						<button
							key={view}
							type="button"
							onClick={() => setActiveView(view)}
							className={cn(
								"px-3 py-1 text-xs capitalize",
								activeView === view ? "font-medium" : "opacity-60",
							)}
							style={{
								background: activeView === view ? "var(--border)" : "var(--bg)",
							}}
						>
							{view}
						</button>
					))}
				</div>
			</div>

			{/* Content */}
			<div className="flex flex-1 overflow-hidden">
				{/* Left: Agent Tree */}
				<div
					className="w-72 shrink-0 border-r overflow-y-auto"
					style={{ borderColor: "var(--border)" }}
				>
					{!topologyDetail && !loading && (
						<div
							className="p-4 text-sm text-center"
							style={{ color: "var(--fg-muted)" }}
						>
							Select a topology to begin editing
						</div>
					)}
					{loading && (
						<div
							className="p-4 text-sm text-center"
							style={{ color: "var(--fg-muted)" }}
						>
							Loading...
						</div>
					)}
					{topologyDetail && activeView === "structure" && (
						<div className="py-2">
							<AgentNode
								agent={topologyDetail.resolved}
								depth={0}
								selectedId={selectedAgentId}
								onSelect={setSelectedAgentId}
							/>
						</div>
					)}
					{topologyDetail && activeView === "relationships" && (
						<div className="p-4 text-sm" style={{ color: "var(--fg-muted)" }}>
							Click an agent in the tree to see relationships.
							<div className="py-2">
								<AgentNode
									agent={topologyDetail.resolved}
									depth={0}
									selectedId={selectedAgentId}
									onSelect={setSelectedAgentId}
								/>
							</div>
						</div>
					)}
					{topologyDetail && activeView === "network" && (
						<div className="p-4 text-sm" style={{ color: "var(--fg-muted)" }}>
							Network view — coming in PR 4
						</div>
					)}
				</div>

				{/* Right: Property Panel */}
				<div className="flex-1 overflow-y-auto p-4">
					{!selectedAgent && topologyDetail && (
						<div
							className="text-sm text-center py-12"
							style={{ color: "var(--fg-muted)" }}
						>
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
			</div>
		</div>
	);
}
