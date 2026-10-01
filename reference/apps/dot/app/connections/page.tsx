import { ConnectionsView } from "./connections-view";

export default async function Connections({
	searchParams,
}: { searchParams: Promise<{ connected?: string }> }) {
	const { connected } = await searchParams;
	return <ConnectionsView justConnected={connected ?? null} />;
}
