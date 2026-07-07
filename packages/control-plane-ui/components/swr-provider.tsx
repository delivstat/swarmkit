"use client";

import { SWRConfig } from "swr";

/**
 * App-wide SWR cache + defaults. One provider means every `useResource` call shares a cache, so
 * concurrent fetches of the same key are deduped (dashboard, instance selector, and per-page lists
 * all hitting `/instances` become one request) and a mutation anywhere revalidates every consumer.
 */
export function SwrProvider({ children }: { children: React.ReactNode }) {
	return (
		<SWRConfig
			value={{
				// Collapse duplicate requests for the same key fired within this window.
				dedupingInterval: 2000,
				revalidateOnFocus: false,
				// Show the last-known data while revalidating instead of flashing a spinner.
				keepPreviousData: true,
			}}
		>
			{children}
		</SWRConfig>
	);
}
