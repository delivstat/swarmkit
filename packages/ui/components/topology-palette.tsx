"use client";

import { GripVertical, Plus, Puzzle, User } from "lucide-react";

import { PALETTE_MIME, type PaletteDrag } from "@/components/topology-canvas";
import { cn } from "@/lib/utils";

/** A palette chip. Primary interaction is **click to add** (reliable everywhere); it is also
 * draggable onto the canvas as a bonus (drop on a specific node to target it). */
function Chip({
	label,
	icon: Icon,
	payload,
	onAdd,
	disabled,
}: {
	label: string;
	icon: typeof User;
	payload: PaletteDrag;
	onAdd: () => void;
	disabled?: boolean;
}) {
	return (
		<button
			type="button"
			draggable={!disabled}
			onClick={onAdd}
			disabled={disabled}
			onDragStart={(e) => {
				e.dataTransfer.setData(PALETTE_MIME, JSON.stringify(payload));
				e.dataTransfer.effectAllowed = "copy";
			}}
			className={cn(
				"group flex w-full items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-left text-xs",
				"transition-colors hover:bg-accent disabled:opacity-50",
				disabled ? "cursor-not-allowed" : "cursor-pointer",
			)}
			title={
				disabled
					? "Select an agent first"
					: "Click to add · or drag onto a node"
			}
		>
			<GripVertical className="size-3 shrink-0 text-muted-foreground" />
			<Icon className="size-3.5 shrink-0" />
			<span className="flex-1 truncate">{label}</span>
			<Plus className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-70" />
		</button>
	);
}

/** The canvas edit palette. Click an archetype (or the blank worker) to add an agent under the
 * selected node (or root); click a skill to attach it to the selected agent. Chips are also draggable
 * onto the canvas — drop on a node to target it specifically. Every add round-trips through the same
 * pure YAML edit ops as the rest of the canvas. */
export function TopologyPalette({
	archetypes,
	skills,
	selectedId,
	onAddAgent,
	onAddSkill,
}: {
	archetypes: string[];
	skills: string[];
	/** The currently-selected agent id — where clicks add. Null ⇒ adds under root. */
	selectedId: string | null;
	onAddAgent: (archetypeId?: string) => void;
	onAddSkill: (skillId: string) => void;
}) {
	const target = selectedId ?? "root";
	return (
		<div className="flex w-56 shrink-0 flex-col gap-3 overflow-y-auto border-r p-3 text-sm">
			<p className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
				Adding under{" "}
				<span className="font-medium text-foreground">{target}</span>. Click a
				node to change target.
			</p>

			<div>
				<p className="mb-1 font-medium">Agents</p>
				<p className="mb-2 text-xs text-muted-foreground">
					Click to add a child · or drag onto a node to nest under it.
				</p>
				<div className="space-y-1">
					<Chip
						label="blank worker"
						icon={User}
						payload={{ kind: "worker" }}
						onAdd={() => onAddAgent()}
					/>
					{archetypes.map((a) => (
						<Chip
							key={a}
							label={a}
							icon={User}
							payload={{ kind: "archetype", value: a }}
							onAdd={() => onAddAgent(a)}
						/>
					))}
				</div>
			</div>

			<div>
				<p className="mb-1 font-medium">Skills</p>
				<p className="mb-2 text-xs text-muted-foreground">
					Click to attach to the selected agent · or drag onto an agent node.
				</p>
				<div className="space-y-1">
					{skills.length === 0 ? (
						<p className="text-xs text-muted-foreground">
							No skills in workspace
						</p>
					) : (
						skills.map((s) => (
							<Chip
								key={s}
								label={s}
								icon={Puzzle}
								payload={{ kind: "skill", value: s }}
								onAdd={() => onAddSkill(s)}
							/>
						))
					)}
				</div>
			</div>
		</div>
	);
}
