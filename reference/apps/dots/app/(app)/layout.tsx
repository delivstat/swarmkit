import { DotsList } from "@/components/sidebar/dots-list";

export default function DotsLayout({
	children,
}: {
	children: React.ReactNode;
}): React.ReactElement {
	return (
		<div className="grid h-screen grid-cols-[240px_1fr]">
			<aside className="border-r border-border bg-muted/20">
				<DotsList />
			</aside>
			<main className="flex min-h-0 flex-col">{children}</main>
		</div>
	);
}
