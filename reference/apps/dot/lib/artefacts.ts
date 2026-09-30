// Result artefact types produced by the handle-item topology. The kind field discriminates
// which ResultPanel layout renders (design/details/dot-app.md §3.3 Action result panel).

export type ArtefactKind =
	| "email_draft"
	| "prep_note"
	| "retrieved_thread"
	| "acknowledgement";

export interface EmailDraftArtefact {
	kind: "email_draft";
	to: string;
	subject: string;
	body: string;
}

export interface PrepNoteArtefact {
	kind: "prep_note";
	title: string;
	sections: { heading: string; bullets: string[] }[];
}

export interface RetrievedThreadArtefact {
	kind: "retrieved_thread";
	title: string;
	summary: string;
	links: { label: string; href: string }[];
}

export interface AcknowledgementArtefact {
	kind: "acknowledgement";
	summary: string;
}

export type Artefact =
	| EmailDraftArtefact
	| PrepNoteArtefact
	| RetrievedThreadArtefact
	| AcknowledgementArtefact;

export interface HandleRunEvent {
	type: "progress" | "result" | "error";
	message?: string;
	artefact?: Artefact;
	detail?: string;
}
