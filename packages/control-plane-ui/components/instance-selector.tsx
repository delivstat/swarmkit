"use client";

import { Server } from "lucide-react";

import { useInstances } from "@/lib/instance-context";

/** Fleet-wide instance switcher in the sidebar. Per-instance pages read the
 * selection from the InstanceProvider. */
export function InstanceSelector() {
	const { instances, selectedId, setSelectedId } = useInstances();

	return (
		<div className="border-b px-3 py-2">
			<label
				htmlFor="instance-switcher"
				className="mb-1 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
			>
				<Server className="size-3" />
				Instance
			</label>
			{instances.length === 0 ? (
				<p className="px-1 py-1.5 text-xs text-muted-foreground">
					None enrolled
				</p>
			) : (
				<select
					id="instance-switcher"
					value={selectedId}
					onChange={(e) => setSelectedId(e.target.value)}
					className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
				>
					{instances.map((i) => (
						<option key={i.id} value={i.id}>
							{i.name}
						</option>
					))}
				</select>
			)}
		</div>
	);
}
