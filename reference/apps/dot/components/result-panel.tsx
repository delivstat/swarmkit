import type { Artefact } from "@/lib/artefacts";

// Renders the last-produced artefact from a handle-item run in a shape that matches its kind
// (design/details/dot-app.md §3.3 Action result panel).
export function ResultPanel({
	artefact,
	progress,
	error,
}: {
	artefact: Artefact | null;
	progress: string | null;
	error: string | null;
}) {
	if (error) {
		return (
			<div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
				<p className="font-medium">Run failed.</p>
				<p className="mt-1 text-destructive/80">{error}</p>
			</div>
		);
	}
	if (!artefact && progress) {
		return (
			<div className="rounded-lg border border-border bg-muted/40 p-4 text-sm text-muted-foreground">
				<span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-foreground/60 align-middle" />
				{progress}
			</div>
		);
	}
	if (!artefact) return null;
	switch (artefact.kind) {
		case "email_draft":
			return <EmailDraftView artefact={artefact} />;
		case "prep_note":
			return <PrepNoteView artefact={artefact} />;
		case "retrieved_thread":
			return <RetrievedThreadView artefact={artefact} />;
		case "acknowledgement":
			return <AckView artefact={artefact} />;
	}
}

function EmailDraftView({
	artefact,
}: {
	artefact: Extract<Artefact, { kind: "email_draft" }>;
}) {
	return (
		<article className="rounded-lg border border-border bg-card p-4">
			<h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
				Draft reply
			</h3>
			<dl className="mt-2 space-y-1 text-sm">
				<div className="flex gap-2">
					<dt className="w-16 shrink-0 text-muted-foreground">To</dt>
					<dd>{artefact.to}</dd>
				</div>
				<div className="flex gap-2">
					<dt className="w-16 shrink-0 text-muted-foreground">Subject</dt>
					<dd className="font-medium">{artefact.subject}</dd>
				</div>
			</dl>
			<pre className="mt-3 whitespace-pre-wrap rounded-md bg-muted/40 p-3 text-sm">
				{artefact.body}
			</pre>
		</article>
	);
}

function PrepNoteView({
	artefact,
}: {
	artefact: Extract<Artefact, { kind: "prep_note" }>;
}) {
	return (
		<article className="rounded-lg border border-border bg-card p-4">
			<h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
				Prep note
			</h3>
			<h4 className="mt-1 text-base font-semibold">{artefact.title}</h4>
			<div className="mt-3 space-y-4">
				{artefact.sections.map((s) => (
					<section key={s.heading}>
						<h5 className="text-sm font-medium">{s.heading}</h5>
						<ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
							{s.bullets.map((b) => (
								<li key={b}>{b}</li>
							))}
						</ul>
					</section>
				))}
			</div>
		</article>
	);
}

function RetrievedThreadView({
	artefact,
}: {
	artefact: Extract<Artefact, { kind: "retrieved_thread" }>;
}) {
	return (
		<article className="rounded-lg border border-border bg-card p-4">
			<h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
				Retrieved conversation
			</h3>
			<h4 className="mt-1 text-base font-semibold">{artefact.title}</h4>
			<p className="mt-2 text-sm text-muted-foreground">{artefact.summary}</p>
			{artefact.links.length > 0 && (
				<ul className="mt-3 space-y-1 text-sm">
					{artefact.links.map((l) => (
						<li key={l.href}>
							<a
								href={l.href}
								target="_blank"
								rel="noreferrer"
								className="underline underline-offset-2 hover:text-foreground"
							>
								{l.label}
							</a>
						</li>
					))}
				</ul>
			)}
		</article>
	);
}

function AckView({
	artefact,
}: {
	artefact: Extract<Artefact, { kind: "acknowledgement" }>;
}) {
	return (
		<div className="rounded-lg border border-border bg-muted/30 p-4 text-sm">
			{artefact.summary}
		</div>
	);
}
