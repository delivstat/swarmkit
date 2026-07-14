import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind class strings, de-duping conflicting utilities (shadcn's standard `cn` helper). */
export function cn(...inputs: ClassValue[]): string {
	return twMerge(clsx(inputs));
}
