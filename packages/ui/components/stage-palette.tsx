"use client";

// The pipeline editor palette (design/details/pipeline-editor-canvas.md §Topology palette): the
// workspace's topologies, each of which drops onto the canvas as a STAGE bound to it. A topology may
// appear as many stages (a stage is an instance, not the topology). Click to add (reliable
// everywhere); drag onto the canvas as a bonus. Every add round-trips through the same pure
// `addStage` mutation as the rest of the editor.

import { Boxes, GripVertical, Plus } from "lucide-react";

import { STAGE_PALETTE_MIME } from "@/components/stage-graph-editor";
import { cn } from "@/lib/utils";

function Chip({
	topology,
	onAdd,
}: {
	topology: string;
	onAdd: () => void;
}) {
	return (
		<button
			type="button"
			draggable
			onClick={onAdd}
			onDragStart={(e) => {
				e.dataTransfer.setData(STAGE_PALETTE_MIME, topology);
				e.dataTransfer.effectAllowed = "copy";
			}}
			className={cn(
				"group flex w-full cursor-pointer items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-left text-xs",
				"transition-colors hover:bg-accent",
			)}
			title="Click to add as a stage · or drag onto the canvas"
		>
			<GripVertical className="size-3 shrink-0 text-muted-foreground" />
			<Boxes className="size-3.5 shrink-0" />
			<span className="flex-1 truncate">{topology}</span>
			<Plus className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-70" />
		</button>
	);
}

/** The topology palette column. Click a topology to append a stage bound to it (with a unique id);
 * chips are also draggable onto the canvas. */
export function StagePalette({
	topologies,
	onAddStage,
}: {
	topologies: string[];
	onAddStage: (topology: string) => void;
}) {
	return (
		<div className="flex w-52 shrink-0 flex-col gap-3 overflow-y-auto border-r p-3 text-sm">
			<p className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
				Drop a topology to add it as a{" "}
				<span className="font-medium text-foreground">stage</span>. Wire stages
				by dragging between handles.
			</p>
			<div>
				<p className="mb-1 font-medium">Topologies</p>
				<div className="space-y-1">
					{topologies.length === 0 ? (
						<p className="text-xs text-muted-foreground">
							No topologies in this workspace.
						</p>
					) : (
						topologies.map((t) => (
							<Chip key={t} topology={t} onAdd={() => onAddStage(t)} />
						))
					)}
				</div>
			</div>
		</div>
	);
}
