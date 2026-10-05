import { loadDots } from "@/lib/dots.config";
import { redirect } from "next/navigation";

export default async function Home(): Promise<never> {
	const dots = await loadDots();
	const first = dots[0];
	if (!first) {
		throw new Error("No Dots configured — see lib/dots.config.ts");
	}
	redirect(`/dots/${first.id}`);
}
