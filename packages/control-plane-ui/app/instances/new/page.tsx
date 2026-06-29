"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { ConnectionMode } from "@/lib/types";

const FIELD = "h-9 rounded-md border border-input bg-background px-2 text-sm";

export default function NewInstancePage() {
	const router = useRouter();
	const [name, setName] = useState("");
	const [endpoint, setEndpoint] = useState("");
	const [connection, setConnection] = useState<ConnectionMode>("direct");
	const [tier, setTier] = useState("read");
	const [tokenRef, setTokenRef] = useState("");
	const [submitting, setSubmitting] = useState(false);
	const [error, setError] = useState<string | null>(null);

	async function submit(e: React.FormEvent) {
		e.preventDefault();
		setSubmitting(true);
		setError(null);
		try {
			const inst = await api.enrollInstance({
				name: name.trim(),
				endpoint: endpoint.trim(),
				connection,
				tier,
				token_ref: tokenRef.trim() || undefined,
			});
			router.push(`/instances/${inst.id}`);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
			setSubmitting(false);
		}
	}

	return (
		<>
			<PageHeader
				title="Enroll instance"
				description="Register a swarmkit serve deployment."
			/>
			<div className="max-w-2xl space-y-6 p-6">
				<Link
					href="/instances"
					className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
				>
					<ArrowLeft className="size-4" />
					All instances
				</Link>

				<Card>
					<CardContent className="pt-6">
						<form onSubmit={submit} className="space-y-4">
							<div className="flex flex-col gap-1">
								<label htmlFor="name" className="text-sm font-medium">
									Name
								</label>
								<input
									id="name"
									value={name}
									onChange={(e) => setName(e.target.value)}
									required
									placeholder="sterling-dc"
									className={FIELD}
								/>
							</div>

							<div className="flex flex-col gap-1">
								<label htmlFor="endpoint" className="text-sm font-medium">
									Endpoint
								</label>
								<input
									id="endpoint"
									value={endpoint}
									onChange={(e) => setEndpoint(e.target.value)}
									placeholder="https://serve.example:8000"
									className={`${FIELD} font-mono text-xs`}
								/>
								<p className="text-xs text-muted-foreground">
									Direct (Mode A) instances are reached here. Poll (Mode B)
									instances connect outbound, so this is informational.
								</p>
							</div>

							<div className="flex gap-4">
								<div className="flex flex-1 flex-col gap-1">
									<label htmlFor="connection" className="text-sm font-medium">
										Connection
									</label>
									<select
										id="connection"
										value={connection}
										onChange={(e) =>
											setConnection(e.target.value as ConnectionMode)
										}
										className={FIELD}
									>
										<option value="direct">
											direct (Mode A — panel pulls)
										</option>
										<option value="poll">
											poll (Mode B — instance connects out)
										</option>
									</select>
								</div>
								<div className="flex flex-1 flex-col gap-1">
									<label htmlFor="tier" className="text-sm font-medium">
										Granted tier
									</label>
									<select
										id="tier"
										value={tier}
										onChange={(e) => setTier(e.target.value)}
										className={FIELD}
									>
										<option value="read">read</option>
										<option value="run">run</option>
										<option value="admin">admin</option>
									</select>
								</div>
							</div>

							<div className="flex flex-col gap-1">
								<label htmlFor="token_ref" className="text-sm font-medium">
									Token ref{" "}
									<span className="text-muted-foreground">(optional)</span>
								</label>
								<input
									id="token_ref"
									value={tokenRef}
									onChange={(e) => setTokenRef(e.target.value)}
									placeholder="env:SWARMKIT_SERVE_TOKEN"
									className={`${FIELD} font-mono text-xs`}
								/>
								<p className="text-xs text-muted-foreground">
									If a direct instance already has a token, give its ref to
									pull-verify now. Otherwise it enrolls unverified — mint a
									token on the next screen, then verify.
								</p>
							</div>

							{error ? (
								<p className="text-sm text-destructive">{error}</p>
							) : null}

							<div className="flex gap-2">
								<Button type="submit" disabled={submitting || !name.trim()}>
									{submitting ? "Enrolling…" : "Enroll"}
								</Button>
								<Button
									type="button"
									variant="ghost"
									onClick={() => router.push("/instances")}
								>
									Cancel
								</Button>
							</div>
						</form>
					</CardContent>
				</Card>
			</div>
		</>
	);
}
