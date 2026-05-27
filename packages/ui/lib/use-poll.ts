"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export function usePoll<T>(
	fetcher: () => Promise<T>,
	intervalMs = 5000,
): {
	data: T | null;
	error: string | null;
	loading: boolean;
	refetch: () => void;
} {
	const [data, setData] = useState<T | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);
	const mountedRef = useRef(true);

	const doFetch = useCallback(async () => {
		try {
			const result = await fetcher();
			if (mountedRef.current) {
				setData(result);
				setError(null);
			}
		} catch (err) {
			if (mountedRef.current) {
				setError(err instanceof Error ? err.message : String(err));
			}
		} finally {
			if (mountedRef.current) setLoading(false);
		}
	}, [fetcher]);

	useEffect(() => {
		mountedRef.current = true;
		doFetch();
		const id = setInterval(doFetch, intervalMs);
		return () => {
			mountedRef.current = false;
			clearInterval(id);
		};
	}, [doFetch, intervalMs]);

	return { data, error, loading, refetch: doFetch };
}
