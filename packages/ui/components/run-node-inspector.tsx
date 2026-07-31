"use client";

// The run-node inspector (design/details/bundled-pipeline-orchestrator.md §4): the read-only detail
// panel for a stage selected on the replay canvas. It shows the stage's status *within this run*,
// its attempt count, its per-node timeline (started / parked / resumed / rejected — the approval
// trail), and the artifact it produced — fetched LAZILY (only on selection) from the ArtifactStore
// via the node endpoint, since a run's artifacts can be large and most are never opened.
//
// A stage PARKED on a multi-party gate also gets the approval panel
// (design/details/pipeline-gate-approval-ui.md) — this is where an approver acts, because it is the
// only surface that shows them what they are approving.
//
// Section ORDER is load-bearing: timeline -> input -> artifact -> approval. The decision comes
// LAST, after the evidence. Putting the buttons above the artifact reproduces the context-free
// inbox this panel exists to replace — you would be clicking Approve before scrolling to the thing
// you are approving.

import { ArrowDownToLine, FileText, Gavel, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { RoleTaskPanel } from "@/components/role-task-panel";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { RUN_STAGE_META, stageStatus, stageTimeline } from "@/lib/run-status";
import type { SagaDetail, SagaNodeArtifact } from "@/lib/types";

export interface RunNodeInspectorProps {
	saga: SagaDetail;
	stageId: string;
}

/** The panel that "plays back" one node: status, attempts, timeline, and its lazy-loaded artifact. */
export function RunNodeInspector({ saga, stageId }: RunNodeInspectorProps) {
	const status = stageStatus(saga, stageId);
	const meta = RUN_STAGE_META[status];
	const timeline = stageTimeline(saga, stageId);
	const ref = saga.artifacts[stageId] ?? null;

	const [artifact, setArtifact] = useState<SagaNodeArtifact | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	// Lazy: fetch this node's input + artifact whenever a stage is selected (a stage may have an
	// input but no output yet — e.g. a failed stage — so we don't gate on the output ref).
	useEffect(() => {
		let live = true;
		setLoading(true);
		setError(null);
		api
			.sagaNode(saga.correlation_id, stageId)
			.then((a) => live && setArtifact(a))
			.catch((e) => live && setError(String(e)))
			.finally(() => live && setLoading(false));
		return () => {
			live = false;
		};
	}, [saga.correlation_id, stageId]);

	return (
		<div className="flex flex-col gap-4 p-4 text-sm">
			<div>
				<div className="flex items-center gap-2">
					<span className="font-mono font-medium">{stageId}</span>
					<Badge
						variant="outline"
						style={{ borderColor: meta.color, color: meta.color }}
					>
						{meta.label}
					</Badge>
					{(saga.attempts[stageId] ?? 0) > 1 ? (
						<span className="text-xs text-muted-foreground">
							{saga.attempts[stageId]} attempts
						</span>
					) : null}
				</div>
			</div>

			<section>
				<h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
					Timeline
				</h4>
				{timeline.length === 0 ? (
					<p className="text-xs text-muted-foreground">
						No timeline entries for this stage yet.
					</p>
				) : (
					<ol className="flex flex-col gap-1.5">
						{timeline.map((t) => (
							<li key={t.seq} className="flex items-baseline gap-2">
								<span className="font-mono text-[11px] tabular-nums text-muted-foreground">
									{t.at.slice(0, 19).replace("T", " ")}
								</span>
								<span className="font-medium">{t.kind}</span>
								{t.detail ? (
									<span className="text-xs text-muted-foreground">
										{t.detail}
									</span>
								) : null}
							</li>
						))}
					</ol>
				)}
			</section>

			<section>
				<h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
					<ArrowDownToLine className="h-3.5 w-3.5" />
					Input
				</h4>
				{loading ? (
					<span className="flex items-center gap-1.5 text-xs text-muted-foreground">
						<Loader2 className="h-3.5 w-3.5 animate-spin" /> loading…
					</span>
				) : error ? (
					<span className="text-xs text-red-500">{error}</span>
				) : artifact?.input ? (
					<pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-3 text-xs">
						{artifact.input}
					</pre>
				) : (
					<p className="text-xs text-muted-foreground">
						This stage ran on empty input
						{stageId === (saga.passed_stages[0] ?? saga.current_stage)
							? " (the pipeline was started without a payload)."
							: "."}
					</p>
				)}
			</section>

			<section>
				<h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
					<FileText className="h-3.5 w-3.5" />
					Artifact
				</h4>
				{!ref ? (
					<p className="text-xs text-muted-foreground">
						This stage has not produced an artifact yet.
					</p>
				) : (
					<div className="flex flex-col gap-2">
						<code className="break-all rounded bg-muted px-1.5 py-0.5 text-[11px]">
							{ref}
						</code>
						{loading ? (
							<span className="flex items-center gap-1.5 text-xs text-muted-foreground">
								<Loader2 className="h-3.5 w-3.5 animate-spin" /> loading…
							</span>
						) : error ? (
							<span className="text-xs text-red-500">{error}</span>
						) : artifact?.content != null ? (
							<pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-3 text-xs">
								{artifact.content}
							</pre>
						) : (
							<span className="text-xs text-muted-foreground">
								(artifact reference recorded; content not resolvable)
							</span>
						)}
					</div>
				)}
			</section>

			{status === "parked" ? (
				<section className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
					<h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
						<Gavel className="h-3.5 w-3.5" />
						Approval
					</h4>
					<p className="mb-3 text-xs text-muted-foreground">
						You are approving the artifact above.
					</p>
					<RoleTaskPanel
						correlationId={saga.correlation_id}
						stageId={stageId}
					/>
				</section>
			) : null}
		</div>
	);
}
