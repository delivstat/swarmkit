"use client";

import { Card, CardTitle } from "@/components/card";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import type { JobResponse } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

function EventStream({ jobId }: { jobId: string }) {
	const [events, setEvents] = useState<string[]>([]);
	const [connected, setConnected] = useState(false);
	const bottomRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		const url = api.jobStreamUrl(jobId);
		const source = new EventSource(url);
		setConnected(true);

		source.onmessage = (e) => {
			setEvents((prev) => [...prev, e.data]);
			if (e.data.startsWith("[done]")) {
				source.close();
				setConnected(false);
			}
		};

		source.onerror = () => {
			source.close();
			setConnected(false);
		};

		return () => source.close();
	}, [jobId]);

	useEffect(() => {
		bottomRef.current?.scrollIntoView({ behavior: "smooth" });
	}, [events.length]);

	return (
		<Card>
			<CardTitle>
				Event Stream{" "}
				{connected && (
					<span
						className="text-xs font-normal ml-2"
						style={{ color: "var(--success)" }}
					>
						live
					</span>
				)}
			</CardTitle>
			<div
				className="font-mono text-xs space-y-1 max-h-64 overflow-y-auto p-2 rounded"
				style={{ background: "var(--bg)" }}
			>
				{events.length === 0 && (
					<p style={{ color: "var(--fg-muted)" }}>Waiting for events...</p>
				)}
				{events.map((event) => (
					<div
						key={`${jobId}-${event}`}
						style={{
							color: event.startsWith("[done]")
								? "var(--success)"
								: "var(--fg)",
						}}
					>
						{event}
					</div>
				))}
				<div ref={bottomRef} />
			</div>
		</Card>
	);
}

export default function JobDetailPage() {
	const params = useParams();
	const jobId = params.id as string;

	const fetchJob = useCallback(() => api.job(jobId), [jobId]);
	const { data: job, error, loading } = usePoll<JobResponse>(fetchJob, 2000);

	return (
		<div>
			<h2 className="text-xl font-bold mb-4">
				Job{" "}
				<span
					className="font-mono text-base"
					style={{ color: "var(--fg-muted)" }}
				>
					{jobId}
				</span>
			</h2>

			{loading && <p className="text-sm opacity-50">Loading...</p>}
			{error && (
				<p className="text-sm" style={{ color: "var(--error)" }}>
					{error}
				</p>
			)}

			{job && (
				<div className="grid gap-4">
					<Card>
						<CardTitle>Status</CardTitle>
						<StatusBadge status={job.status} />
					</Card>

					{job.output && (
						<Card>
							<CardTitle>Output</CardTitle>
							<pre
								className="text-sm whitespace-pre-wrap p-3 rounded max-h-96 overflow-y-auto"
								style={{ background: "var(--bg)" }}
							>
								{job.output}
							</pre>
						</Card>
					)}

					{job.error && (
						<Card>
							<CardTitle>Error</CardTitle>
							<pre
								className="text-sm whitespace-pre-wrap p-3 rounded"
								style={{ background: "var(--bg)", color: "var(--error)" }}
							>
								{job.error}
							</pre>
						</Card>
					)}

					<EventStream jobId={jobId} />
				</div>
			)}
		</div>
	);
}
