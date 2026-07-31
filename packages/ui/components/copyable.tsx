"use client";

// A copy affordance for the panes that hold a run's real content — a stage's input, the artifact it
// produced, a job's output or error. These are the things an operator actually needs out of the UI
// (into a ticket, a diff, a prompt), and selecting a scrolling <pre> by hand is both fiddly and
// lossy. One component so every pane behaves the same way.

import { Check, Copy } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/** Copy *text*, resolving true on success. Falls back to a hidden textarea + execCommand, because
 * navigator.clipboard is unavailable on a non-secure origin — which is exactly where a workspace
 * portal usually runs (http://127.0.0.1:8000, or a LAN host). */
export async function copyText(text: string): Promise<boolean> {
	try {
		if (navigator.clipboard?.writeText) {
			await navigator.clipboard.writeText(text);
			return true;
		}
	} catch {
		// fall through to the legacy path
	}
	try {
		const el = document.createElement("textarea");
		el.value = text;
		el.setAttribute("readonly", "");
		el.style.position = "fixed";
		el.style.opacity = "0";
		document.body.appendChild(el);
		el.select();
		const ok = document.execCommand("copy");
		document.body.removeChild(el);
		return ok;
	} catch {
		return false;
	}
}

export interface CopyButtonProps {
	value: string;
	className?: string;
	label?: string;
}

/** A small copy control that confirms, then resets. */
export function CopyButton({ value, className, label }: CopyButtonProps) {
	const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

	useEffect(() => {
		if (state === "idle") return;
		const id = setTimeout(() => setState("idle"), 1600);
		return () => clearTimeout(id);
	}, [state]);

	const onClick = useCallback(async () => {
		setState((await copyText(value)) ? "copied" : "failed");
	}, [value]);

	return (
		<button
			type="button"
			onClick={onClick}
			disabled={!value}
			title={value ? `Copy ${label ?? "to clipboard"}` : "Nothing to copy"}
			aria-label={`Copy ${label ?? "to clipboard"}`}
			className={cn(
				"inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px]",
				"text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
				"disabled:pointer-events-none disabled:opacity-40",
				state === "copied" && "border-emerald-500/50 text-emerald-500",
				state === "failed" && "border-destructive/50 text-destructive",
				className,
			)}
		>
			{state === "copied" ? (
				<Check className="h-3 w-3" />
			) : (
				<Copy className="h-3 w-3" />
			)}
			{state === "copied" ? "Copied" : state === "failed" ? "Failed" : "Copy"}
		</button>
	);
}

export interface CopyablePreProps {
	value: string;
	className?: string;
	label?: string;
}

/** A scrollable `<pre>` with a copy control pinned to its top-right.
 *
 * The button floats over the content rather than sitting in the heading, so it stays reachable
 * while the pane is scrolled — the long outputs are the ones worth copying. */
export function CopyablePre({ value, className, label }: CopyablePreProps) {
	return (
		<div className="group relative">
			<CopyButton
				value={value}
				label={label}
				className="absolute right-2 top-2 z-10 bg-background/80 backdrop-blur-sm"
			/>
			<pre
				className={cn(
					"overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-3 pr-20 text-xs",
					className,
				)}
			>
				{value}
			</pre>
		</div>
	);
}
