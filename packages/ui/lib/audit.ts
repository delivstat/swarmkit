/**
 * What an audit row actually says, extracted for display.
 *
 * The store has carried the full M6 event since M6 — policy decision, inputs, outputs, verdict,
 * reasoning, confidence, model, tokens, cost, duration, error — and `/audit` serialized nine
 * header fields and dropped every one of them. So the log rendered as a list of event types: a
 * reader could see THAT a skill ran and never what it was asked or what it answered.
 *
 * These helpers turn one event into the two things a reader wants: a one-line summary for the row
 * itself, and an ordered set of sections for when they open it.
 */

import type { AuditEvent } from "./types";

export interface DetailSection {
	label: string;
	/** Pre-formatted for a <pre>. JSON is stringified here, not in the component. */
	body: string;
}

/** Whether an event carries anything worth expanding. A header-only event should not offer a
 * disclosure that opens onto nothing. */
export function hasDetail(event: AuditEvent): boolean {
	return detailSections(event).length > 0;
}

function json(value: unknown): string {
	return JSON.stringify(value, null, 2);
}

function isEmpty(value: unknown): boolean {
	if (value === null || value === undefined || value === "") return true;
	if (typeof value === "object")
		return Object.keys(value as object).length === 0;
	return false;
}

/** The expanded view, in the order a reader asks the questions: what was decided, what went in,
 * what came back, why, and what it cost. */
export function detailSections(event: AuditEvent): DetailSection[] {
	const out: DetailSection[] = [];
	const push = (label: string, value: unknown, format = json) => {
		if (!isEmpty(value)) out.push({ label, body: format(value) });
	};

	if (event.policy_reason)
		out.push({ label: "Policy", body: event.policy_reason });
	push("Inputs", event.inputs);
	push("Outputs", event.outputs);
	if (event.reasoning) out.push({ label: "Reasoning", body: event.reasoning });
	push("Error", event.error);
	// The payload is the older, unstructured half of the same event. Shown last and only when it
	// holds something the structured fields do not.
	push("Payload", event.payload);
	return out;
}

/** Tokens and cost as one string, or null when the event is not a model call. */
export function formatModelCost(event: AuditEvent): string | null {
	const model = event.model_name ?? event.model_provider;
	const tokens =
		event.tokens_in !== null || event.tokens_out !== null
			? `${(event.tokens_in ?? 0).toLocaleString()} in / ${(event.tokens_out ?? 0).toLocaleString()} out`
			: null;
	const cost =
		event.cost_usd !== null && event.cost_usd !== undefined
			? event.cost_usd > 0 && event.cost_usd < 0.01
				? "<$0.01"
				: `$${event.cost_usd.toFixed(2)}`
			: null;
	const parts = [model, tokens, cost].filter(Boolean);
	return parts.length > 0 ? parts.join(" · ") : null;
}

/** Duration as ms or s. Null when never recorded — which is different from zero. */
export function formatDuration(ms: number | null | undefined): string | null {
	if (ms === null || ms === undefined) return null;
	return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

type AttachmentRecord = {
	name?: string;
	media_type?: string;
	size?: number;
};

/** Bytes as a person reads them. Rounded hard — this is a table cell, not a storage report. */
function formatBytes(size: number): string {
	if (size < 1024) return `${size} B`;
	if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
	return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * What the caller put in front of the model, on one line.
 *
 * Without this the row renders as `run.attachments | runtime | - | -` — an event that exists
 * precisely so somebody can see what the model was shown, showing nothing. The digest is in the
 * expanded payload rather than here: it is what makes the reference checkable, not what makes the
 * row readable.
 */
function summarizeAttachments(event: AuditEvent): string {
	const records =
		(event.payload?.attachments as AttachmentRecord[] | undefined) ?? [];
	if (records.length === 0) return "no attachments";

	const describe = (a: AttachmentRecord) => {
		const detail = [
			a.media_type,
			a.size !== undefined ? formatBytes(a.size) : null,
		]
			.filter(Boolean)
			.join(", ");
		return detail
			? `${a.name ?? "attachment"} (${detail})`
			: (a.name ?? "attachment");
	};

	if (records.length === 1) return describe(records[0] as AttachmentRecord);
	return `${records.length} attachments: ${records.map((a) => a.name ?? "attachment").join(", ")}`;
}

/**
 * The one-line summary shown on the row itself, so the table is readable without expanding
 * anything.
 *
 * Ordered by what distinguishes one row from its neighbours: a decision skill's verdict, then a
 * governance denial, then the first input argument, then the skill's own id.
 */
export function summarize(event: AuditEvent): string {
	if (event.verdict) {
		const confidence =
			event.confidence !== null && event.confidence !== undefined
				? ` (${Math.round(event.confidence * 100)}%)`
				: "";
		return `${event.verdict}${confidence}${event.reasoning ? ` — ${event.reasoning}` : ""}`;
	}
	if (event.policy_decision === "deny") {
		return `denied${event.policy_reason ? ` — ${event.policy_reason}` : ""}`;
	}
	if (event.error && Object.keys(event.error).length > 0) {
		return Object.values(event.error).join(": ");
	}
	if (event.event_type === "run.attachments") {
		return summarizeAttachments(event);
	}
	const inputs =
		event.inputs ??
		(event.payload?.inputs as Record<string, unknown> | undefined);
	if (inputs && Object.keys(inputs).length > 0) {
		const [key, value] = Object.entries(inputs)[0] as [string, unknown];
		const rendered = typeof value === "string" ? value : JSON.stringify(value);
		return `${key}: ${rendered}`;
	}
	return event.skill_id ?? "";
}

/** A long summary truncated for a table cell, on a word boundary where one is near the cut. */
export function truncate(text: string, max = 90): string {
	if (text.length <= max) return text;
	const cut = text.slice(0, max);
	const space = cut.lastIndexOf(" ");
	return `${(space > max - 20 ? cut.slice(0, space) : cut).trimEnd()}…`;
}
