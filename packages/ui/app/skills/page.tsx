"use client";

import { Card } from "@/components/card";
import { api } from "@/lib/api";
import type { SkillItem } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useCallback } from "react";

const CATEGORY_COLORS: Record<string, string> = {
	capability: "var(--accent)",
	decision: "var(--warning)",
	coordination: "var(--success)",
	persistence: "var(--fg-muted)",
};

export default function SkillsPage() {
	const fetchSkills = useCallback(() => api.skills(), []);
	const { data, error, loading } = usePoll<SkillItem[]>(fetchSkills, 30000);

	return (
		<div>
			<h2 className="text-xl font-bold mb-4">Skills</h2>
			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{data && (
				<div className="grid grid-cols-2 gap-3">
					{data.map((skill) => (
						<Card key={skill.id}>
							<div className="flex items-center justify-between">
								<span className="font-medium">{skill.id}</span>
								<span
									className="text-xs px-2 py-0.5 rounded-full border"
									style={{
										color: CATEGORY_COLORS[skill.category] ?? "var(--fg-muted)",
										borderColor: `${CATEGORY_COLORS[skill.category] ?? "var(--fg-muted)"}40`,
									}}
								>
									{skill.category || "unknown"}
								</span>
							</div>
						</Card>
					))}
				</div>
			)}
		</div>
	);
}
