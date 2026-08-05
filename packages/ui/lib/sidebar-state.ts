/**
 * Whether the sidebar is collapsed, remembered across navigations and reloads.
 *
 * Kept out of the component so it can be tested without a DOM, and so the SSR rule lives in one
 * place: the server has no `window`, so the first render must always assume expanded and let an
 * effect correct it. Reading storage during render would produce markup that disagrees with the
 * client's and make React discard the tree.
 */

const STORAGE_KEY = "swarmkit.sidebar.collapsed";

/** The stored preference, or `null` when there isn't one — which is not the same as `false`.
 * A user who has never touched it gets the default; a user who chose expanded keeps that choice
 * even if the default later changes. */
export function loadCollapsed(): boolean | null {
	if (typeof window === "undefined") return null;
	try {
		const raw = window.localStorage.getItem(STORAGE_KEY);
		return raw === null ? null : raw === "true";
	} catch {
		// Private-mode Safari and locked-down browsers throw on access. A preference that cannot
		// be read is a preference that does not exist; the sidebar still works.
		return null;
	}
}

export function storeCollapsed(collapsed: boolean): void {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(STORAGE_KEY, String(collapsed));
	} catch {
		// Losing the preference must never cost the click that set it.
	}
}

/** Width classes for the two states. Collapsed leaves room for a 16px icon plus its padding. */
export const SIDEBAR_WIDTH = { expanded: "w-56", collapsed: "w-14" } as const;

/** Whether a nav entry is the current page.
 *
 * Prefix-matched so `/jobs/history` still highlights Jobs — but on a path BOUNDARY, or `/run`
 * would light up for `/runs` and two entries would look active at once.
 */
export function isActive(pathname: string, href: string): boolean {
	return pathname === href || pathname.startsWith(`${href}/`);
}
