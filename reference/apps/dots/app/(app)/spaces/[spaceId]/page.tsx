import { notFound } from "next/navigation";

import { loadDots } from "@/lib/dots.config";
import { findSpace } from "@/lib/spaces.config";

import { SpaceView } from "./space-view";

export default async function SpacePage({
	params,
}: {
	params: Promise<{ spaceId: string }>;
}): Promise<React.ReactElement> {
	const { spaceId } = await params;
	const space = await findSpace(spaceId);
	if (!space) notFound();
	const dots = await loadDots();
	return <SpaceView space={space} dots={dots} />;
}
