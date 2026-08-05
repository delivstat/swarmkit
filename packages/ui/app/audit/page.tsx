"use client";

import { useCallback, useState } from "react";

import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import {
	detailSections,
	formatDuration,
	formatModelCost,
	hasDetail,
	summarize,
	truncate,
} from "@/lib/audit";
import type { AuditEvent } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

/**
 * The audit log, with what each event was actually FOR.
 *
 * The table used to show event type, agent, topology, run and time — the header of an event and
 * none of its content. A reader could see that `skill.executed` happened and not what the skill
 * was asked or what it answered, which is most of why anyone opens this page. Every one of those
 * fields had been in the store since M6; `/audit` simply did not return them.
 *
 * So: a **Detail** column that summarises the row inline, and a disclosure that opens the full
 * inputs, outputs, reasoning and error. Read-only throughout — the media pillar exposes no edit or
 * delete, and nothing here writes.
 */

function DetailRow({ event }: { event: AuditEvent }) {
	const sections = detailSections(event);
	const cost = formatModelCost(event);
	const duration = formatDuration(event.duration_ms);

	return (
		<tr className="border-t bg-muted/30">
			<td colSpan={7} className="px-4 py-3">
				<div className="space-y-3">
					{(cost || duration || event.skill_category) && (
						<div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
							{event.skill_category && <span>{event.skill_category}</span>}
							{duration && <span>{duration}</span>}
							{cost && <span>{cost}</span>}
						</div>
					)}
					{sections.map((section) => (
						<div key={section.label}>
							<div className="mb-1 text-xs font-medium text-muted-foreground">
								{section.label}
							</div>
							<pre className="max-h-64 overflow-auto rounded-md border bg-background p-2 text-xs whitespace-pre-wrap">
								{section.body}
							</pre>
						</div>
					))}
				</div>
			</td>
		</tr>
	);
}

export default function AuditPage() {
	const fetchAudit = useCallback(() => api.audit({ limit: 200 }), []);
	const { data, error, loading } = usePoll<AuditEvent[]>(fetchAudit, 5000);
	const [expanded, setExpanded] = useState<Set<string>>(new Set());

	const toggle = (id: string) =>
		setExpanded((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	return (
		<div>
			<h2 className="mb-4 text-xl font-bold">Audit log</h2>
			<p className="mb-4 text-sm text-muted-foreground">
				Append-only, newest first. Read-only — the media pillar exposes no edit
				or delete. Click a row for its inputs, outputs and reasoning.
			</p>

			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}
			{data && data.length === 0 && (
				<p className="text-sm text-muted-foreground">No audit events yet.</p>
			)}

			{data && data.length > 0 && (
				<div className="overflow-hidden rounded-lg border">
					<table className="w-full text-sm">
						<thead>
							<tr className="bg-muted text-muted-foreground">
								<th className="px-4 py-2 text-left font-medium">Event</th>
								<th className="px-4 py-2 text-left font-medium">Agent</th>
								<th className="px-4 py-2 text-left font-medium">Skill</th>
								<th className="px-4 py-2 text-left font-medium">Detail</th>
								<th className="px-4 py-2 text-left font-medium">Policy</th>
								<th className="px-4 py-2 text-left font-medium">Run</th>
								<th className="px-4 py-2 text-left font-medium">Time</th>
							</tr>
						</thead>
						<tbody>
							{data.map((event) => {
								const open = expanded.has(event.event_id);
								const expandable = hasDetail(event);
								return [
									<tr
										key={event.event_id}
										className={`border-t ${expandable ? "cursor-pointer hover:bg-muted/50" : ""}`}
										// A disclosure has to work from the keyboard too — the log
										// is a read surface people tab through.
										tabIndex={expandable ? 0 : undefined}
										onClick={
											expandable ? () => toggle(event.event_id) : undefined
										}
										onKeyDown={
											expandable
												? (e) => {
														if (e.key === "Enter" || e.key === " ") {
															e.preventDefault();
															toggle(event.event_id);
														}
													}
												: undefined
										}
									>
										<td className="px-4 py-2 font-mono text-xs">
											{expandable && (
												<span className="mr-1 inline-block w-3 text-muted-foreground">
													{open ? "▾" : "▸"}
												</span>
											)}
											{event.event_type}
										</td>
										<td className="px-4 py-2">
											{event.agent_id}
											{event.agent_role ? (
												<span className="text-muted-foreground">
													{" "}
													({event.agent_role})
												</span>
											) : null}
										</td>
										<td className="px-4 py-2 font-mono text-xs">
											{event.skill_id ?? "-"}
										</td>
										<td className="px-4 py-2 text-xs text-muted-foreground">
											{truncate(summarize(event)) || "-"}
										</td>
										<td className="px-4 py-2 text-xs">
											{event.policy_decision ? (
												<StatusBadge status={event.policy_decision} />
											) : (
												// Null is not "allowed" — it means no decision was
												// ever recorded, and conflating the two would be a
												// lie in a governance record.
												<span className="text-muted-foreground">-</span>
											)}
										</td>
										<td className="px-4 py-2 font-mono text-xs text-muted-foreground">
											{event.run_id ? event.run_id.slice(0, 12) : "-"}
										</td>
										<td className="px-4 py-2 text-xs text-muted-foreground">
											{event.timestamp
												? new Date(event.timestamp).toLocaleString()
												: "-"}
										</td>
									</tr>,
									open ? (
										<DetailRow key={`${event.event_id}-detail`} event={event} />
									) : null,
								];
							})}
						</tbody>
					</table>
				</div>
			)}
		</div>
	);
}
