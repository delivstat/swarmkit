import { DOTS } from "@/lib/dots.config";
import { redirect } from "next/navigation";

export default function Home(): never {
	const first = DOTS[0];
	if (!first) {
		throw new Error("No Dots configured — see lib/dots.config.ts");
	}
	redirect(`/dots/${first.id}`);
}
