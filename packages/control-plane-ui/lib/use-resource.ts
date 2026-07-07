"use client";

import useSWR from "swr";

export interface Resource<T> {
	/** Latest data, or `undefined` before the first successful load. */
	data: T | undefined;
	/** Error message from the most recent failed fetch, else `null`. */
	error: string | null;
	/** True only during the first load (no data yet). Use for skeletons. */
	loading: boolean;
	/** True while any fetch (incl. background revalidation) is in flight. */
	validating: boolean;
	/** Revalidate this resource now (e.g. after a mutation). Stable across renders. */
	refresh: () => void;
}

/**
 * SWR-backed data fetching — the replacement for `usePoll`.
 *
 * `usePoll` re-ran its effect whenever the inline `fetcher` changed identity (i.e. every render),
 * thrashing the interval and re-fetching; it had no shared cache (every component polling the same
 * endpoint fetched independently), no request-ordering guard (a slow response could latch over a
 * newer one), and its `refresh` closed over a changing `load`. `useResource` keys the cache by a
 * stable string, so SWR dedupes concurrent requests across the whole app, ignores fetcher identity,
 * orders responses, and gives a stable `refresh` (`mutate`). Pass `key = null` to disable the fetch
 * (conditional data — e.g. before an id is known).
 */
export function useResource<T>(
	key: string | null,
	fetcher: () => Promise<T>,
	opts?: { refreshInterval?: number },
): Resource<T> {
	const { data, error, isLoading, isValidating, mutate } = useSWR<T, Error>(
		key,
		key ? () => fetcher() : null,
		{
			refreshInterval: opts?.refreshInterval ?? 5000,
			revalidateOnFocus: false,
			keepPreviousData: true,
		},
	);
	return {
		data,
		error: error ? error.message : null,
		loading: isLoading,
		validating: isValidating,
		refresh: () => {
			void mutate();
		},
	};
}
