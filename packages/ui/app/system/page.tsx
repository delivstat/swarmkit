"use client";

import { useCallback, useState } from "react";

import { Card } from "@/components/card";
import { api } from "@/lib/api";
import type { EnvVarStatus, SystemReport } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

/**
 * System — what this instance is running, where its data goes, and what its environment says.
 *
 * The storage table exists because a misconfigured workspace looked identical to an empty one:
 * `storage.runtime: postgres` was ignored, every run landed in a SQLite file on one machine, and
 * every page here rendered "no runs" without an error anywhere. The answer to "why is this empty"
 * has to be reachable from the screen that is empty.
 *
 * The environment table is the same argument one level down: env vars are invisible config, and
 * without a list of what the runtime even reads, "it behaves differently on that machine" has
 * nowhere to start. Values come from a curated server-side registry — secrets report set/unset
 * only, and connection URLs arrive with the password already masked.
 */
export default function SystemPage() {
	const fetchSystem = useCallback(() => api.system(), []);
	const { data, error, loading } = usePoll<SystemReport>(fetchSystem, 60000);
	const [showUnset, setShowUnset] = useState(false);

	const env = data?.environment ?? [];
	const visible = showUnset ? env : env.filter((v) => v.set);
	const groups = [...new Set(visible.map((v) => v.group))];
	const setCount = env.filter((v) => v.set).length;

	return (
		<div>
			<h2 className="mb-4 text-xl font-bold">System</h2>

			{loading && <p className="text-sm text-muted-foreground">Loading…</p>}
			{error && <p className="text-sm text-destructive">{error}</p>}

			<Card className="mb-6">
				<h3 className="mb-3 text-sm font-semibold">Versions</h3>
				<dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
					<dt className="text-muted-foreground">Workspace</dt>
					<dd className="font-mono">{data?.workspace ?? "—"}</dd>
					<dt className="text-muted-foreground">Runtime</dt>
					<dd className="font-mono">{data?.runtime_version || "unknown"}</dd>
					<dt className="text-muted-foreground">Web UI</dt>
					<dd className="font-mono">{data?.webui_version || "unknown"}</dd>
				</dl>
			</Card>

			<h3 className="mb-2 text-sm font-semibold">Storage</h3>
			<p className="mb-3 text-sm text-muted-foreground">
				Where each store resolves to, and which setting decided it. Postgres
				URLs are shown with the password masked.
			</p>

			{data && data.storage.warnings.length > 0 && (
				<Card className="mb-4 border-amber-500/40 bg-amber-500/5">
					<h4 className="mb-2 text-sm font-semibold">
						Rows exist somewhere this instance is not reading
					</h4>
					<ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
						{data.storage.warnings.map((w) => (
							<li key={w}>{w}</li>
						))}
					</ul>
					<p className="mt-3 text-sm text-muted-foreground">
						Copy them across with{" "}
						<code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
							swarmkit storage migrate &lt;workspace&gt;
						</code>
						.
					</p>
				</Card>
			)}

			{data && (
				<div className="mb-8 overflow-hidden rounded-lg border">
					<table className="w-full text-sm">
						<thead>
							<tr className="bg-muted text-muted-foreground">
								<th className="px-4 py-2 text-left font-medium">Store</th>
								<th className="px-4 py-2 text-left font-medium">Backend</th>
								<th className="px-4 py-2 text-left font-medium">Location</th>
								<th className="px-4 py-2 text-left font-medium">Source</th>
							</tr>
						</thead>
						<tbody>
							{data.storage.stores.map((row) => (
								<tr key={row.store} className="border-t">
									<td className="px-4 py-2 font-medium">{row.store}</td>
									<td className="px-4 py-2">{row.backend}</td>
									<td className="break-all px-4 py-2 font-mono text-xs">
										{row.location}
									</td>
									<td className="px-4 py-2 text-muted-foreground">
										{row.source}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}

			<h3 className="mb-2 text-sm font-semibold">Workspace properties</h3>
			<p className="mb-3 text-sm text-muted-foreground">
				From <code className="font-mono text-xs">workspace.env.yaml</code>, with{" "}
				<code className="font-mono text-xs">
					${"{"}ENV_VAR{"}"}
				</code>{" "}
				already resolved — what the run actually used, not what was typed. Read
				from the file on every request, so a parameter a new feature adds
				appears here on its own.
			</p>
			{data && data.properties.length === 0 && (
				<Card className="mb-8">
					<p className="text-sm text-muted-foreground">
						No <code className="font-mono text-xs">workspace.env.yaml</code> in
						this workspace.
					</p>
				</Card>
			)}
			{data && data.properties.length > 0 && (
				<div className="mb-8 overflow-hidden rounded-lg border">
					<table className="w-full text-sm">
						<tbody>
							{data.properties.map((prop) => (
								<tr key={prop.name} className="border-t first:border-t-0">
									<td className="w-[22rem] px-4 py-2 font-mono text-xs">
										{prop.name}
									</td>
									<td className="break-all px-4 py-2 font-mono text-xs">
										<span
											className={prop.sensitive ? "text-muted-foreground" : ""}
										>
											{prop.value}
										</span>
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}

			<div className="mb-2 flex items-baseline justify-between">
				<h3 className="text-sm font-semibold">Runtime environment variables</h3>
				{env.length > 0 && (
					<button
						type="button"
						onClick={() => setShowUnset((v) => !v)}
						className="text-xs text-muted-foreground underline-offset-2 hover:underline"
					>
						{showUnset
							? `Show only the ${setCount} that are set`
							: `Show all ${env.length}, including unset`}
					</button>
				)}
			</div>
			<p className="mb-3 text-sm text-muted-foreground">
				The variables this runtime reads. Credentials are never sent to this
				page — they report only whether they are set.
			</p>

			{data && setCount === 0 && !showUnset && (
				<Card>
					<p className="text-sm text-muted-foreground">
						None of the known variables are set — this instance is running
						entirely on workspace.yaml and defaults.
					</p>
				</Card>
			)}

			{groups.map((group) => (
				<div key={group} className="mb-4">
					<h4 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
						{group}
					</h4>
					<div className="overflow-hidden rounded-lg border">
						<table className="w-full text-sm">
							<tbody>
								{visible
									.filter((v) => v.group === group)
									.map((v) => (
										<EnvRow key={v.name} entry={v} />
									))}
							</tbody>
						</table>
					</div>
				</div>
			))}
		</div>
	);
}

function EnvRow({ entry }: { entry: EnvVarStatus }) {
	return (
		<tr className="border-t first:border-t-0 align-top">
			<td className="w-[22rem] px-4 py-2">
				<span className="font-mono text-xs">{entry.name}</span>
				<p className="mt-0.5 text-xs text-muted-foreground">
					{entry.description}
				</p>
			</td>
			<td className="break-all px-4 py-2 font-mono text-xs">
				{entry.set ? (
					<span className={entry.sensitive ? "text-muted-foreground" : ""}>
						{entry.value}
					</span>
				) : (
					<span className="text-muted-foreground">not set</span>
				)}
			</td>
		</tr>
	);
}
