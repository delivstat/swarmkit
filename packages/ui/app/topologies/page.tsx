"use client";

import { Card, CardTitle } from "@/components/card";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/use-poll";
import Link from "next/link";
import { useCallback, useState } from "react";

function RunDialog({
	topology,
	onClose,
}: {
	topology: string;
	onClose: () => void;
}) {
	const [input, setInput] = useState("");
	const [submitting, setSubmitting] = useState(false);
	const [result, setResult] = useState<string | null>(null);

	const submit = async () => {
		if (!input.trim()) return;
		setSubmitting(true);
		try {
			const job = await api.run(topology, input);
			setResult(`Job ${job.job_id} started`);
		} catch (err) {
			setResult(`Error: ${err instanceof Error ? err.message : String(err)}`);
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<div
			className="fixed inset-0 flex items-center justify-center z-50"
			style={{ background: "rgba(0,0,0,0.5)" }}
		>
			<Card className="w-[480px]">
				<CardTitle>Run {topology}</CardTitle>
				<textarea
					className="w-full p-2 rounded text-sm border mb-3 resize-none"
					style={{
						background: "var(--bg)",
						borderColor: "var(--border)",
						color: "var(--fg)",
					}}
					rows={4}
					placeholder="Enter input for the topology..."
					value={input}
					onChange={(e) => setInput(e.target.value)}
				/>
				{result && (
					<p className="text-xs mb-3" style={{ color: "var(--fg-muted)" }}>
						{result}
					</p>
				)}
				<div className="flex gap-2 justify-end">
					<button
						type="button"
						onClick={onClose}
						className="px-3 py-1.5 text-sm rounded border"
						style={{ borderColor: "var(--border)" }}
					>
						Close
					</button>
					<button
						type="button"
						onClick={submit}
						disabled={submitting || !input.trim()}
						className="px-3 py-1.5 text-sm rounded font-medium disabled:opacity-40"
						style={{ background: "var(--accent)", color: "var(--accent-fg)" }}
					>
						{submitting ? "Running..." : "Run"}
					</button>
				</div>
			</Card>
		</div>
	);
}

export default function TopologiesPage() {
	const fetchTopologies = useCallback(() => api.topologies(), []);
	const { data, error, loading } = usePoll<string[]>(fetchTopologies, 30000);
	const [runTarget, setRunTarget] = useState<string | null>(null);

	return (
		<div>
			<h2 className="text-xl font-bold mb-4">Topologies</h2>
			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}
			{data && (
				<div className="grid grid-cols-2 gap-3">
					{data.map((name) => (
						<Card key={name}>
							<div className="flex items-center justify-between">
								<span className="font-medium">{name}</span>
								<div className="flex gap-2">
									<Link
										href={`/composer?topology=${name}`}
										className="text-xs px-2.5 py-1 rounded font-medium"
										style={{
											border: "1px solid var(--border)",
										}}
									>
										Edit
									</Link>
									<button
										type="button"
										onClick={() => setRunTarget(name)}
										className="text-xs px-2.5 py-1 rounded font-medium"
										style={{
											background: "var(--accent)",
											color: "var(--accent-fg)",
										}}
									>
										Run
									</button>
								</div>
							</div>
						</Card>
					))}
				</div>
			)}
			{runTarget && (
				<RunDialog topology={runTarget} onClose={() => setRunTarget(null)} />
			)}
		</div>
	);
}
