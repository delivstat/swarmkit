"use client";

import type { Dot } from "@/lib/dots.config";
import { useCallback, useRef, useState } from "react";

interface Turn {
	id: string;
	role: "user" | "assistant";
	content: string;
	streaming?: boolean;
}

export function DotChat({ dot }: { dot: Dot }): React.ReactElement {
	// Chat state is intentionally thin for Phase 3: one turn in, one turn out, streaming via
	// AG-UI SSE. CopilotKit's own `<CopilotChat>` component arrives in Phase 3b once the agent
	// adapter spike (see design/details/dots-app.md Q1) picks the wiring shape.
	const [turns, setTurns] = useState<Turn[]>([
		{ id: "greeting", role: "assistant", content: dot.greeting },
	]);
	const [input, setInput] = useState("");
	const [busy, setBusy] = useState(false);
	const abortRef = useRef<AbortController | null>(null);

	const send = useCallback(async () => {
		const text = input.trim();
		if (!text || busy) return;
		const userTurn: Turn = { id: `u-${Date.now()}`, role: "user", content: text };
		const assistantId = `a-${Date.now()}`;
		setTurns((prev) => [
			...prev,
			userTurn,
			{ id: assistantId, role: "assistant", content: "", streaming: true },
		]);
		setInput("");
		setBusy(true);
		const abort = new AbortController();
		abortRef.current = abort;
		try {
			const res = await fetch("/api/ag-ui", {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify({ dotId: dot.id, message: text }),
				signal: abort.signal,
			});
			if (!res.ok || !res.body) throw new Error(`${res.status}`);
			const reader = res.body.getReader();
			const decoder = new TextDecoder();
			let buffer = "";
			while (true) {
				const { value, done } = await reader.read();
				if (done) break;
				buffer += decoder.decode(value, { stream: true });
				let sep = buffer.indexOf("\n\n");
				while (sep !== -1) {
					const frame = buffer.slice(0, sep).trim();
					buffer = buffer.slice(sep + 2);
					for (const line of frame.split("\n")) {
						if (!line.startsWith("data:")) continue;
						const event = JSON.parse(line.slice(5).trim()) as {
							type: string;
							delta?: string;
						};
						if (event.type === "TextMessageContent" && event.delta) {
							setTurns((prev) =>
								prev.map((t) =>
									t.id === assistantId ? { ...t, content: t.content + (event.delta ?? "") } : t,
								),
							);
						}
					}
					sep = buffer.indexOf("\n\n");
				}
			}
		} catch (err) {
			setTurns((prev) =>
				prev.map((t) =>
					t.id === assistantId ? { ...t, content: `error: ${(err as Error).message}` } : t,
				),
			);
		} finally {
			setTurns((prev) => prev.map((t) => (t.id === assistantId ? { ...t, streaming: false } : t)));
			setBusy(false);
			abortRef.current = null;
		}
	}, [busy, dot.id, input]);

	return (
		<div className="flex min-h-0 flex-1 flex-col">
			<div className="flex-1 space-y-4 overflow-y-auto p-6">
				{turns.map((t) => (
					<div key={t.id} className={t.role === "user" ? "flex justify-end" : "flex justify-start"}>
						<div
							className={
								t.role === "user"
									? "max-w-[70%] rounded-lg bg-primary px-4 py-2 text-primary-foreground"
									: "max-w-[70%] rounded-lg bg-muted px-4 py-2"
							}
						>
							<p className="whitespace-pre-wrap text-sm">
								{t.content}
								{t.streaming ? <span className="animate-pulse">▍</span> : null}
							</p>
						</div>
					</div>
				))}
			</div>
			<form
				className="flex gap-2 border-t border-border p-4"
				onSubmit={(e) => {
					e.preventDefault();
					void send();
				}}
			>
				<input
					className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
					placeholder={`Ask ${dot.name}…`}
					value={input}
					onChange={(e) => setInput(e.target.value)}
					disabled={busy}
				/>
				<button
					type="submit"
					className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
					disabled={busy || !input.trim()}
				>
					Send
				</button>
			</form>
		</div>
	);
}
