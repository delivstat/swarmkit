"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

export function CopyButton({
	value,
	label = "Copy",
}: { value: string; label?: string }) {
	const [copied, setCopied] = useState(false);

	async function copy() {
		await navigator.clipboard.writeText(value);
		setCopied(true);
		setTimeout(() => setCopied(false), 1500);
	}

	return (
		<Button variant="outline" size="sm" onClick={copy}>
			{copied ? <Check /> : <Copy />}
			{copied ? "Copied" : label}
		</Button>
	);
}
