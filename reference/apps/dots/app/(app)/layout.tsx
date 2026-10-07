import { DotsList } from "@/components/sidebar/dots-list";
import { loadDots } from "@/lib/dots.config";
import { loadSpaces } from "@/lib/spaces.config";

export default async function DotsLayout({
	children,
}: {
	children: React.ReactNode;
}): Promise<React.ReactElement> {
	const [dots, spaces] = await Promise.all([loadDots(), loadSpaces()]);
	return (
		<div className="grid h-screen grid-cols-[240px_1fr]">
			<aside className="border-r border-border bg-muted/20">
				<DotsList dots={dots} spaces={spaces} />
			</aside>
			<main className="flex min-h-0 flex-col">{children}</main>
		</div>
	);
}
