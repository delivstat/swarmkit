"use client";

import { useCallback, useEffect, useState } from "react";

import { Card, CardTitle } from "@/components/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { buildMemoryTree, groupByType, memoryKey } from "@/lib/memory-tree";
import type {
	MemoryChange,
	MemoryItem,
	MemoryQuarantineItem,
} from "@/lib/types";

/** Governed memory (design/details/governed-memory.md) — read-only browse + the curator's quarantine
 * review. The same store + JSON as `swarmkit memory` and the /memory endpoints. Memory is never
 * edited here (agents write it through the governed path); the one write is resolving a contradiction. */
export default function MemoryPage() {
	return (
		<div>
			<h2 className="mb-1 text-xl font-bold">Governed memory</h2>
			<p className="mb-4 text-sm text-muted-foreground">
				Facts that evolve in place over time. Browse the current state and each
				fact&apos;s timeline, and resolve contradictions parked for the curator.
				Read-only — the same data as <code>swarmkit memory</code>.
			</p>
			<Tabs defaultValue="browse">
				<TabsList className="mb-4">
					<TabsTrigger value="browse">Browse</TabsTrigger>
					<TabsTrigger value="quarantine">Quarantine</TabsTrigger>
				</TabsList>
				<TabsContent value="browse">
					<BrowseTab />
				</TabsContent>
				<TabsContent value="quarantine">
					<QuarantineTab />
				</TabsContent>
			</Tabs>
		</div>
	);
}

// ── op badge colours (the reconcile ops) ─────────────────────────────────────────────────────────
const OP_COLOR: Record<string, string> = {
	new: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
	reinforce: "bg-sky-500/15 text-sky-400 border-sky-500/30",
	update: "bg-amber-500/15 text-amber-400 border-amber-500/30",
	refine: "bg-violet-500/15 text-violet-400 border-violet-500/30",
	contradict: "bg-red-500/15 text-red-400 border-red-500/30",
};

function OpBadge({ op }: { op: string }) {
	return (
		<span
			className={`rounded border px-1.5 py-0.5 font-mono text-xs ${OP_COLOR[op] ?? "border-border text-muted-foreground"}`}
		>
			{op}
		</span>
	);
}

// ── Browse: tree / category + a fact's timeline ──────────────────────────────────────────────────
function BrowseTab() {
	const [memories, setMemories] = useState<MemoryItem[]>([]);
	const [query, setQuery] = useState("");
	const [view, setView] = useState<"tree" | "category">("tree");
	const [selected, setSelected] = useState<MemoryItem | null>(null);
	const [error, setError] = useState<string | null>(null);

	const fetchIt = useCallback(async (q: string) => {
		setError(null);
		try {
			const d = await api.searchMemory(q);
			setMemories(d.memories);
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		}
	}, []);

	// Search as you type (debounced) — relevance-ranked on the server; empty lists all by confidence.
	useEffect(() => {
		const t = setTimeout(() => void fetchIt(query), 250);
		return () => clearTimeout(t);
	}, [query, fetchIt]);

	const groups =
		view === "tree"
			? buildMemoryTree(memories)
			: groupByType(memories).map((g) => ({ subject: g.type, items: g.items }));

	return (
		<div>
			<div className="mb-4 flex items-center gap-2">
				<Input
					placeholder="Search memory (relevance-ranked, live)…"
					value={query}
					onChange={(e) => setQuery(e.target.value)}
					className="max-w-sm"
				/>
				<div className="ml-auto flex gap-1">
					<Button
						variant={view === "tree" ? "default" : "outline"}
						size="sm"
						onClick={() => setView("tree")}
					>
						By subject
					</Button>
					<Button
						variant={view === "category" ? "default" : "outline"}
						size="sm"
						onClick={() => setView("category")}
					>
						By category
					</Button>
				</div>
			</div>

			{error && <p className="mb-3 text-sm text-red-400">{error}</p>}

			<div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
				<Card>
					<CardTitle>
						{memories.length} fact{memories.length === 1 ? "" : "s"}
					</CardTitle>
					<div className="mt-3 max-h-[60vh] overflow-y-auto pr-1">
						{groups.length === 0 && (
							<p className="text-sm text-muted-foreground">No memories.</p>
						)}
						{groups.map((g) => (
							<div key={g.subject} className="mb-3">
								<div className="mb-1 font-mono text-xs uppercase tracking-wide text-muted-foreground">
									{g.subject}
								</div>
								<ul className="flex flex-col gap-1">
									{g.items.map((m) => {
										const active =
											selected != null && memoryKey(selected) === memoryKey(m);
										return (
											<li key={m.key}>
												<button
													type="button"
													onClick={() => setSelected(m)}
													className={`w-full rounded border px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent ${active ? "border-sky-500/50 bg-accent" : "border-transparent"}`}
												>
													<span className="font-medium">{m.attribute}</span>{" "}
													<span className="text-muted-foreground">
														= {m.value}
													</span>
													<span className="ml-2 text-xs text-muted-foreground">
														{m.type} · {(m.confidence * 100) | 0}% · ×
														{m.reinforce_count}
														{m.source ? ` · via ${m.source}` : ""}
													</span>
												</button>
											</li>
										);
									})}
								</ul>
							</div>
						))}
					</div>
				</Card>

				<Card>
					<CardTitle>Timeline</CardTitle>
					{selected ? (
						<FactTimeline item={selected} />
					) : (
						<p className="mt-3 text-sm text-muted-foreground">
							Select a fact to see how it evolved.
						</p>
					)}
				</Card>
			</div>
		</div>
	);
}

function FactTimeline({ item }: { item: MemoryItem }) {
	const [history, setHistory] = useState<MemoryChange[] | null>(null);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		let live = true;
		setHistory(null);
		setError(null);
		api
			.getMemoryItem(item.subject, item.attribute, true)
			.then((d) => live && setHistory(d.history))
			.catch(
				(e) => live && setError(e instanceof Error ? e.message : String(e)),
			);
		return () => {
			live = false;
		};
	}, [item.subject, item.attribute]);

	return (
		<div className="mt-3">
			<div className="mb-3 text-sm">
				<span className="font-mono text-xs text-muted-foreground">
					{item.subject}
				</span>
				<div className="font-medium">
					{item.attribute} = {item.value}
				</div>
				<div className="text-xs text-muted-foreground">
					{item.type} · confidence {(item.confidence * 100) | 0}% · reinforced ×
					{item.reinforce_count} · since {item.valid_from.slice(0, 10)}
				</div>
				<div className="mt-1 text-xs text-muted-foreground">
					inserted by{" "}
					<span className="text-foreground">{item.source ?? "—"}</span>{" "}
					<span className="opacity-70">
						(the change log below shows who decided each step)
					</span>
				</div>
			</div>
			{error && <p className="text-sm text-red-400">{error}</p>}
			{history == null && !error && (
				<p className="text-sm text-muted-foreground">Loading…</p>
			)}
			{history && (
				<ol className="relative ml-2 border-l border-border">
					{history.map((c) => (
						<li key={c.id} className="mb-3 ml-4">
							<div className="absolute -left-[5px] mt-1.5 h-2 w-2 rounded-full bg-border" />
							<div className="flex items-center gap-2">
								<OpBadge op={c.op} />
								<span className="text-xs text-muted-foreground">
									{c.timestamp.slice(0, 19).replace("T", " ")}
								</span>
								<span className="text-xs text-muted-foreground">
									({c.decided_by})
								</span>
							</div>
							<div className="mt-1 text-sm">
								{c.before != null && (
									<>
										<span className="text-muted-foreground line-through">
											{String(c.before.value ?? "")}
										</span>{" "}
										→{" "}
									</>
								)}
								<span className="font-medium">
									{String(c.after.value ?? "")}
								</span>
							</div>
							{c.reason && (
								<div className="text-xs text-muted-foreground">{c.reason}</div>
							)}
						</li>
					))}
				</ol>
			)}
		</div>
	);
}

// ── Quarantine: the curator's contradiction queue ────────────────────────────────────────────────
function QuarantineTab() {
	const [items, setItems] = useState<MemoryQuarantineItem[]>([]);
	const [status, setStatus] = useState("pending");
	const [resolvedBy, setResolvedBy] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState<number | null>(null);

	const fetchIt = useCallback(async (s: string) => {
		setError(null);
		try {
			const d = await api.listQuarantine(s);
			setItems(d.quarantine);
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		}
	}, []);

	useEffect(() => {
		void fetchIt(status);
	}, [fetchIt, status]);

	async function resolve(id: number, accept: boolean) {
		if (!resolvedBy.trim()) {
			setError("Enter your curator identity before resolving.");
			return;
		}
		setBusy(id);
		setError(null);
		try {
			await api.resolveQuarantine(id, resolvedBy.trim(), accept);
			await fetchIt(status);
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setBusy(null);
		}
	}

	return (
		<div>
			<div className="mb-4 flex flex-wrap items-center gap-2">
				<Input
					placeholder="Your curator identity (e.g. curator:alice)"
					value={resolvedBy}
					onChange={(e) => setResolvedBy(e.target.value)}
					className="max-w-xs"
				/>
				<div className="ml-auto flex gap-1">
					{["pending", "accepted", "rejected"].map((s) => (
						<Button
							key={s}
							variant={status === s ? "default" : "outline"}
							size="sm"
							onClick={() => setStatus(s)}
						>
							{s}
						</Button>
					))}
				</div>
			</div>

			{error && <p className="mb-3 text-sm text-red-400">{error}</p>}

			{items.length === 0 ? (
				<p className="text-sm text-muted-foreground">
					No {status} contradictions.
				</p>
			) : (
				<div className="flex flex-col gap-3">
					{items.map((q) => (
						<Card key={q.id}>
							<div className="flex items-start justify-between gap-4">
								<div className="min-w-0">
									<div className="flex items-center gap-2">
										<OpBadge op="contradict" />
										<span className="font-mono text-xs text-muted-foreground">
											#{q.id} · {q.memory_key}
										</span>
									</div>
									<div className="mt-2 text-sm">
										<span className="text-red-400">proposed:</span>{" "}
										<span className="font-medium">
											{String(q.candidate.value ?? "")}
										</span>
									</div>
									<div className="text-sm">
										<span className="text-emerald-400">trusted:</span>{" "}
										<span className="font-medium">{q.current_value}</span>
									</div>
									{q.reasoning && (
										<div className="mt-1 text-xs text-muted-foreground">
											{q.reasoning}
										</div>
									)}
									{q.resolved_by && (
										<div className="mt-1 text-xs text-muted-foreground">
											{q.status} by {q.resolved_by}
										</div>
									)}
								</div>
								{q.status === "pending" && (
									<div className="flex shrink-0 gap-2">
										<Button
											size="sm"
											variant="outline"
											disabled={busy === q.id}
											onClick={() => void resolve(q.id, true)}
										>
											Accept
										</Button>
										<Button
											size="sm"
											variant="destructive"
											disabled={busy === q.id}
											onClick={() => void resolve(q.id, false)}
										>
											Reject
										</Button>
									</div>
								)}
							</div>
						</Card>
					))}
				</div>
			)}
		</div>
	);
}
