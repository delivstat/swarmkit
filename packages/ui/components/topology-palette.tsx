"use client";

import { GripVertical, Puzzle, User } from "lucide-react";

import { PALETTE_MIME, type PaletteDrag } from "@/components/topology-canvas";
import { cn } from "@/lib/utils";

/** A single draggable palette chip. Sets the shared drag payload so the canvas' drop handler knows
 * what to add and where. */
function Chip({
	label,
	icon: Icon,
	payload,
}: {
	label: string;
	icon: typeof User;
	payload: PaletteDrag;
}) {
	return (
		<div
			draggable
			onDragStart={(e) => {
				e.dataTransfer.setData(PALETTE_MIME, JSON.stringify(payload));
				e.dataTransfer.effectAllowed = "copy";
			}}
			className={cn(
				"flex cursor-grab items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-xs",
				"transition-colors hover:bg-accent active:cursor-grabbing",
			)}
			title="Drag onto the canvas"
		>
			<GripVertical className="size-3 shrink-0 text-muted-foreground" />
			<Icon className="size-3.5 shrink-0" />
			<span className="truncate">{label}</span>
		</div>
	);
}

/** The canvas edit palette: draggable archetypes (+ a blank worker) that drop onto the canvas to add
 * an agent, and skills that drop onto an agent node to attach. Round-trips through the same YAML edit
 * ops as every other canvas change. */
export function TopologyPalette({
	archetypes,
	skills,
}: {
	archetypes: string[];
	skills: string[];
}) {
	return (
		<div className="flex w-56 shrink-0 flex-col gap-3 overflow-y-auto border-r p-3 text-sm">
			<div>
				<p className="mb-1 font-medium">Agents</p>
				<p className="mb-2 text-xs text-muted-foreground">
					Drag onto the canvas — drop on a node to nest under it, else under the
					selected agent.
				</p>
				<div className="space-y-1">
					<Chip label="blank worker" icon={User} payload={{ kind: "worker" }} />
					{archetypes.map((a) => (
						<Chip
							key={a}
							label={a}
							icon={User}
							payload={{ kind: "archetype", value: a }}
						/>
					))}
				</div>
			</div>

			<div>
				<p className="mb-1 font-medium">Skills</p>
				<p className="mb-2 text-xs text-muted-foreground">
					Drag onto an agent node to attach it.
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
							/>
						))
					)}
				</div>
			</div>
		</div>
	);
}
