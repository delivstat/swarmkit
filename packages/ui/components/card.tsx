import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

export function Card({
	children,
	className,
}: {
	children: ReactNode;
	className?: string;
}) {
	return (
		<div
			className={cn("rounded-lg border p-4", className)}
			style={{
				background: "var(--bg-card)",
				borderColor: "var(--border)",
			}}
		>
			{children}
		</div>
	);
}

export function CardTitle({ children }: { children: ReactNode }) {
	return (
		<h3
			className="text-sm font-semibold mb-3"
			style={{ color: "var(--fg-muted)" }}
		>
			{children}
		</h3>
	);
}
