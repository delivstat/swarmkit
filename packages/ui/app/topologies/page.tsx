"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { Card } from "@/components/card";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/use-poll";

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
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Run {topology}</DialogTitle>
				</DialogHeader>
				<Textarea
					rows={4}
					placeholder="Enter input for the topology…"
					value={input}
					onChange={(e) => setInput(e.target.value)}
				/>
				{result && <p className="text-xs text-muted-foreground">{result}</p>}
				<DialogFooter>
					<Button type="button" variant="outline" onClick={onClose}>
						Close
					</Button>
					<Button
						type="button"
						onClick={submit}
						disabled={submitting || !input.trim()}
					>
						{submitting ? "Running…" : "Run"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

export default function TopologiesPage() {
	const fetchTopologies = useCallback(() => api.topologies(), []);
	const { data, error, loading } = usePoll<string[]>(fetchTopologies, 30000);
	const [runTarget, setRunTarget] = useState<string | null>(null);

	return (
		<div>
			<h2 className="mb-4 text-xl font-bold">Topologies</h2>
			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}
			{data && (
				<div className="grid grid-cols-2 gap-3">
					{data.map((name) => (
						<Card key={name}>
							<div className="flex items-center justify-between">
								<span className="font-medium">{name}</span>
								<div className="flex gap-2">
									<Button asChild variant="outline" size="sm">
										<Link href={`/composer?topology=${name}`}>Edit</Link>
									</Button>
									<Button
										type="button"
										size="sm"
										onClick={() => setRunTarget(name)}
									>
										Run
									</Button>
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
