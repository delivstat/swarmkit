import { DotCopilotChat } from "@/components/chat/dot-copilot-chat";
import { findDot } from "@/lib/dots.config";
import { notFound } from "next/navigation";

export default async function DotPage({
	params,
}: {
	params: Promise<{ dotId: string }>;
}): Promise<React.ReactElement> {
	const { dotId } = await params;
	const dot = findDot(dotId);
	if (!dot) notFound();
	return (
		<div className="flex min-h-0 flex-1 flex-col">
			<header className="border-b border-border px-6 py-4">
				<h1 className="text-lg font-semibold">{dot.name}</h1>
				<p className="text-sm text-muted-foreground">{dot.role}</p>
			</header>
			<DotCopilotChat dot={dot} />
		</div>
	);
}
