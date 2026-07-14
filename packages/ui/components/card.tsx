import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

// Lightweight card used across dashboard surfaces. For richer composition (header/footer/description)
// use the shadcn primitive in `@/components/ui/card`; this keeps the simple children API those
// surfaces already rely on, now on design-system tokens.
export function Card({
	children,
	className,
}: {
	children: ReactNode;
	className?: string;
}) {
	return (
		<div
			className={cn(
				"rounded-xl border bg-card p-4 text-card-foreground shadow-sm",
				className,
			)}
		>
			{children}
		</div>
	);
}

export function CardTitle({ children }: { children: ReactNode }) {
	return (
		<h3 className="mb-3 text-sm font-semibold text-muted-foreground">
			{children}
		</h3>
	);
}
