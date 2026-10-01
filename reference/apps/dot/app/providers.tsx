"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

// Design note pins React Query for server-state (§State management). One client per app,
// created inside the client boundary so the same instance survives navigation but a hard reload
// resets everything.
export function Providers({ children }: { children: React.ReactNode }) {
	const [qc] = useState(
		() =>
			new QueryClient({
				defaultOptions: {
					queries: {
						staleTime: 15_000,
						refetchOnWindowFocus: true,
						retry: 1,
					},
				},
			}),
	);
	return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
