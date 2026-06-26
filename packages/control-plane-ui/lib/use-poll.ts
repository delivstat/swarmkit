"use client";

import { useCallback, useEffect, useState } from "react";

export interface PollState<T> {
	data: T | null;
	error: string | null;
	loading: boolean;
	refresh: () => void;
}

/** Fetch once on mount, then re-fetch every `intervalMs`. Mirrors packages/ui. */
export function usePoll<T>(
	fetcher: () => Promise<T>,
	intervalMs = 5000,
): PollState<T> {
	const [data, setData] = useState<T | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);

	const load = useCallback(async () => {
		try {
			setData(await fetcher());
			setError(null);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setLoading(false);
		}
	}, [fetcher]);

	useEffect(() => {
		load();
		const timer = setInterval(load, intervalMs);
		return () => clearInterval(timer);
	}, [load, intervalMs]);

	return { data, error, loading, refresh: load };
}
