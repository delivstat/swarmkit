"use client";

// The stage inspector (design/details/pipeline-editor-canvas.md §Per-stage configuration): the node
// panel for the selected stage. Connections (signal/external/loop) are drawn on the canvas; this
// panel edits the stage's NODE PROPERTIES — id, topology, gate, locks, release_locks_on,
// compensation — plus the two connection kinds that read best as lists (external entries and loops
// re-entering this stage). Every control applies a pure mutation from lib/stage-graph-edit.ts and
// hands the new document back via `onChange`, so the YAML stays authoritative and round-trips.

import { Trash2, X } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import type { RefOptions } from "@/lib/schema-form";
import {
	type StageGraphDoc,
	emittedSignals,
	externalEntries,
	readLoops,
	readStages,
} from "@/lib/stage-graph";
import {
	addExternalEntry,
	addLock,
	addLoop,
	deleteLoop,
	removeLock,
	removeStage,
	removeWhenEvent,
	renameStage,
	setCompensation,
	setGate,
	setLoopWhen,
	setReleaseLocksOn,
	setStageTopology,
} from "@/lib/stage-graph-edit";

const NONE = "__none__";

/** Every event name the graph knows: emitted signals ∪ all `when` events ∪ loop triggers. Drives the
 * release-on + loop-trigger dropdowns (design: "a dropdown of events known to the graph"). */
function knownEvents(doc: StageGraphDoc): string[] {
	const set = new Set<string>(emittedSignals(doc));
	for (const s of readStages(doc)) for (const w of s.when) set.add(w);
	for (const l of readLoops(doc)) set.add(l.when);
	return [...set].sort();
}

function EventSelect({
	value,
	options,
	placeholder,
	allowNone = true,
	onChange,
}: {
	value: string | null;
	options: string[];
	placeholder: string;
	allowNone?: boolean;
	onChange: (value: string | null) => void;
}) {
	// Keep the current value selectable even if it is not in the workspace list (an unknown ref).
	const items =
		value && !options.includes(value) ? [value, ...options] : options;
	return (
		<Select
			value={value ?? NONE}
			onValueChange={(v) => onChange(v === NONE ? null : v)}
		>
			<SelectTrigger className="h-8 text-xs">
				<SelectValue placeholder={placeholder} />
			</SelectTrigger>
			<SelectContent>
				{allowNone ? (
					<SelectItem value={NONE} className="text-muted-foreground">
						— none —
					</SelectItem>
				) : null}
				{items.map((o) => (
					<SelectItem key={o} value={o}>
						{o}
					</SelectItem>
				))}
			</SelectContent>
		</Select>
	);
}

function Section({
	title,
	children,
}: {
	title: string;
	children: React.ReactNode;
}) {
	return (
		<div className="space-y-1.5">
			<Label className="text-xs uppercase tracking-wide text-muted-foreground">
				{title}
			</Label>
			{children}
		</div>
	);
}

export interface StageInspectorProps {
	doc: StageGraphDoc;
	stageId: string | null;
	refOptions: RefOptions;
	onChange: (next: Record<string, unknown>) => void;
	/** Called after the stage is renamed so the page can re-point its selection. */
	onRenamed: (newId: string) => void;
	/** Called after the stage is deleted so the page can clear its selection. */
	onDeleted: () => void;
}

export function StageInspector({
	doc,
	stageId,
	refOptions,
	onChange,
	onRenamed,
	onDeleted,
}: StageInspectorProps) {
	const [idDraft, setIdDraft] = useState("");
	const [lockDraft, setLockDraft] = useState("");
	const [entryDraft, setEntryDraft] = useState("");
	const [loopDraft, setLoopDraft] = useState("");

	if (!stageId) {
		return (
			<div className="p-4 text-sm text-muted-foreground">
				Click a stage to configure it. Drop a topology from the palette to add
				one; drag between handles to wire signals and loops.
			</div>
		);
	}
	const stage = readStages(doc).find((s) => s.id === stageId);
	if (!stage) {
		return (
			<div className="p-4 text-sm text-muted-foreground">
				Stage <span className="font-medium">{stageId}</span> is no longer in the
				graph.
			</div>
		);
	}

	const doc0 = (doc ?? {}) as Record<string, unknown>;
	const topologies = refOptions.topology ?? [];
	const funnels = refOptions.funnel ?? [];
	const events = knownEvents(doc);
	const externalsHere = externalEntries(doc).filter((e) => e.stage === stageId);
	const loopsHere = readLoops(doc).filter((l) => l.to === stageId);

	const commitRename = () => {
		const trimmed = idDraft.trim();
		if (!trimmed || trimmed === stageId) {
			setIdDraft("");
			return;
		}
		onChange(renameStage(doc0, stageId, trimmed));
		onRenamed(trimmed);
		setIdDraft("");
	};

	return (
		<div className="space-y-4 p-4 text-sm">
			<Section title="Stage id">
				<div className="flex items-center gap-2">
					<Input
						className="h-8 font-mono text-xs"
						value={idDraft || stageId}
						onChange={(e) => setIdDraft(e.target.value)}
						onBlur={commitRename}
						onKeyDown={(e) => {
							if (e.key === "Enter") commitRename();
							if (e.key === "Escape") setIdDraft("");
						}}
						aria-label="Stage id"
					/>
				</div>
				<p className="text-[10px] text-muted-foreground">
					Renaming rewrites any loops that re-enter this stage.
				</p>
			</Section>

			<Section title="Topology">
				<EventSelect
					value={stage.topology}
					options={topologies}
					placeholder="pick a topology"
					allowNone={false}
					onChange={(v) => v && onChange(setStageTopology(doc0, stageId, v))}
				/>
			</Section>

			<Section title="Gate (funnel)">
				<EventSelect
					value={stage.gate}
					options={funnels}
					placeholder="no gate"
					onChange={(v) => onChange(setGate(doc0, stageId, v))}
				/>
			</Section>

			<Section title="Compensation (topology)">
				<EventSelect
					value={stage.compensation}
					options={topologies}
					placeholder="no compensation"
					onChange={(v) => onChange(setCompensation(doc0, stageId, v))}
				/>
			</Section>

			<Section title="Locks">
				<div className="flex flex-wrap gap-1">
					{stage.locks.length === 0 ? (
						<span className="text-xs text-muted-foreground">none</span>
					) : (
						stage.locks.map((l) => (
							<Badge key={l} variant="secondary" className="gap-1 font-mono">
								{l}
								<button
									type="button"
									onClick={() => onChange(removeLock(doc0, stageId, l))}
									aria-label={`remove lock ${l}`}
								>
									<X size={11} />
								</button>
							</Badge>
						))
					)}
				</div>
				<div className="flex items-center gap-2">
					<Input
						className="h-8 font-mono text-xs"
						placeholder="contract:oms-web"
						value={lockDraft}
						onChange={(e) => setLockDraft(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === "Enter" && lockDraft.trim()) {
								onChange(addLock(doc0, stageId, lockDraft.trim()));
								setLockDraft("");
							}
						}}
					/>
					<Button
						type="button"
						size="sm"
						variant="outline"
						disabled={!lockDraft.trim()}
						onClick={() => {
							onChange(addLock(doc0, stageId, lockDraft.trim()));
							setLockDraft("");
						}}
					>
						Add
					</Button>
				</div>
			</Section>

			<Section title="Release locks on">
				<EventSelect
					value={stage.releaseLocksOn}
					options={events}
					placeholder="no release event"
					onChange={(v) => onChange(setReleaseLocksOn(doc0, stageId, v))}
				/>
			</Section>

			<Section title="External entries (inbound webhooks)">
				<div className="space-y-1">
					{externalsHere.length === 0 ? (
						<span className="text-xs text-muted-foreground">none</span>
					) : (
						externalsHere.map((e) => (
							<div
								key={e.event}
								className="flex items-center gap-2 rounded-md border px-2 py-1"
							>
								<span className="flex-1 truncate font-mono text-xs">
									{e.event}
								</span>
								<button
									type="button"
									onClick={() =>
										onChange(removeWhenEvent(doc0, stageId, e.event))
									}
									aria-label={`remove external entry ${e.event}`}
								>
									<X size={12} className="text-muted-foreground" />
								</button>
							</div>
						))
					)}
				</div>
				<div className="flex items-center gap-2">
					<Input
						className="h-8 font-mono text-xs"
						placeholder="requirement.created"
						value={entryDraft}
						onChange={(e) => setEntryDraft(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === "Enter" && entryDraft.trim()) {
								onChange(addExternalEntry(doc0, stageId, entryDraft.trim()));
								setEntryDraft("");
							}
						}}
					/>
					<Button
						type="button"
						size="sm"
						variant="outline"
						disabled={!entryDraft.trim()}
						onClick={() => {
							onChange(addExternalEntry(doc0, stageId, entryDraft.trim()));
							setEntryDraft("");
						}}
					>
						Add
					</Button>
				</div>
			</Section>

			<Section title="Loops into this stage (defect re-entry)">
				<div className="space-y-1">
					{loopsHere.length === 0 ? (
						<span className="text-xs text-muted-foreground">none</span>
					) : (
						loopsHere.map((l) => (
							<div
								key={l.when}
								className="flex items-center gap-2 rounded-md border px-2 py-1"
							>
								<div className="flex-1">
									<EventSelect
										value={l.when}
										options={events}
										placeholder="trigger event"
										allowNone={false}
										onChange={(v) =>
											v && onChange(setLoopWhen(doc0, stageId, l.when, v))
										}
									/>
								</div>
								<button
									type="button"
									onClick={() => onChange(deleteLoop(doc0, stageId, l.when))}
									aria-label={`remove loop on ${l.when}`}
								>
									<X size={12} className="text-muted-foreground" />
								</button>
							</div>
						))
					)}
				</div>
				<div className="flex items-center gap-2">
					<Input
						className="h-8 font-mono text-xs"
						placeholder="defect.raised"
						value={loopDraft}
						onChange={(e) => setLoopDraft(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === "Enter" && loopDraft.trim()) {
								onChange(addLoop(doc0, stageId, loopDraft.trim()));
								setLoopDraft("");
							}
						}}
					/>
					<Button
						type="button"
						size="sm"
						variant="outline"
						disabled={!loopDraft.trim()}
						onClick={() => {
							onChange(addLoop(doc0, stageId, loopDraft.trim()));
							setLoopDraft("");
						}}
					>
						Add loop
					</Button>
				</div>
			</Section>

			<div className="border-t pt-3">
				<Button
					type="button"
					size="sm"
					variant="outline"
					className="text-destructive"
					onClick={() => {
						onChange(removeStage(doc0, stageId));
						onDeleted();
					}}
				>
					<Trash2 size={13} /> Delete stage
				</Button>
			</div>
		</div>
	);
}
