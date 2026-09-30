"use client";

import { useState } from "react";

export interface ChatMessage {
	from: "system" | "owner" | "assistant";
	text: string;
}

// Scoped chat surface for an item (design/details/dot-app.md §3.3). Multi-line textarea autogrows
// up to 5 lines. Enter sends on desktop; Shift+Enter inserts a newline. Mobile keeps a Send tap.
export function ChatPane({
	messages,
	onSend,
	pending,
}: {
	messages: ChatMessage[];
	onSend: (text: string) => void;
	pending: boolean;
}) {
	const [text, setText] = useState("");

	function submit() {
		const t = text.trim();
		if (!t || pending) return;
		onSend(t);
		setText("");
	}

	return (
		<div className="flex h-full flex-col">
			<ol
				aria-label="Chat log"
				role="log"
				aria-live="polite"
				className="flex-1 space-y-2 overflow-y-auto p-3"
			>
				{messages.map((m, i) => (
					<li
						key={`${m.from}-${i}-${m.text.slice(0, 24)}`}
						className={
							m.from === "owner"
								? "ml-auto max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
								: m.from === "assistant"
									? "mr-auto max-w-[85%] rounded-lg bg-muted px-3 py-2 text-sm"
									: "mx-auto max-w-full px-3 py-1 text-center text-xs italic text-muted-foreground"
						}
					>
						{m.text}
					</li>
				))}
				{pending && (
					<li className="mr-auto max-w-[85%] rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
						<span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-foreground/60 align-middle" />
						thinking…
					</li>
				)}
			</ol>
			<form
				className="border-t border-border p-3"
				onSubmit={(e) => {
					e.preventDefault();
					submit();
				}}
			>
				<textarea
					rows={1}
					className="block w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
					placeholder="Ask about this item…"
					value={text}
					onChange={(e) => setText(e.target.value)}
					onKeyDown={(e) => {
						if (e.key === "Enter" && !e.shiftKey) {
							e.preventDefault();
							submit();
						}
					}}
					style={{ maxHeight: "8rem" }}
				/>
				<div className="mt-2 flex justify-end">
					<button
						type="submit"
						disabled={pending || !text.trim()}
						className="inline-flex min-h-11 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow disabled:opacity-60"
					>
						Send
					</button>
				</div>
			</form>
		</div>
	);
}
