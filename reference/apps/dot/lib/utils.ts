import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn's convention: merge conditional Tailwind classes with tailwind-merge conflict resolution. */
export function cn(...inputs: ClassValue[]) {
	return twMerge(clsx(inputs));
}
