"use client";

import { Card } from "@/components/card";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/use-poll";
import { useCallback } from "react";

export default function ArchetypesPage() {
	const fetchArchetypes = useCallback(() => api.archetypes(), []);
	const { data, error, loading } = usePoll<string[]>(fetchArchetypes, 30000);

	return (
		<div>
			<h2 className="text-xl font-bold mb-4">Archetypes</h2>
			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{data && (
				<div className="grid grid-cols-3 gap-3">
					{data.map((name) => (
						<Card key={name}>
							<span className="font-medium">{name}</span>
						</Card>
					))}
				</div>
			)}
		</div>
	);
}
