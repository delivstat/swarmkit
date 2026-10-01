import { ItemView } from "./item-view";

export default async function ItemDetail({
	params,
}: { params: Promise<{ id: string }> }) {
	const { id } = await params;
	return <ItemView id={id} />;
}
