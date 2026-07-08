"use client";

import { Radio, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { JoinCodeResult } from "@/lib/types";

const FIELD = "h-9 rounded-md border border-input bg-background px-2 text-sm";
const TIERS = ["read", "run", "admin"];

function panelUrl(): string {
	const configured = process.env.NEXT_PUBLIC_CONTROL_PLANE_API;
	if (configured) return configured;
	if (typeof window !== "undefined") return window.location.origin;
	return "<panel-url>";
}

/**
 * Mode B enrollment (design 19). The operator mints a one-time join code here and hands it to a
 * NAT'd edge, which self-registers by calling the panel outbound: `swarmkit connect <panel>
 * --join-code <code>`. No inbound reachability to the instance is needed — the panel never has to
 * reach the edge. The panel issues the connector credential when the edge joins.
 */
export function JoinCodePanel() {
	const [name, setName] = useState("");
	const [tier, setTier] = useState("read");
	const [minting, setMinting] = useState(false);
	const [result, setResult] = useState<JoinCodeResult | null>(null);
	const [error, setError] = useState<string | null>(null);

	async function mint() {
		setMinting(true);
		setError(null);
		try {
			setResult(
				await api.mintJoinCode({ name: name.trim() || undefined, tier }),
			);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setMinting(false);
		}
	}

	const connectCmd = result
		? `swarmkit connect ${panelUrl()} --join-code ${result.join_code}`
		: "";

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2">
					<Radio className="size-4" />
					Enroll a poll (Mode B) instance
				</CardTitle>
			</CardHeader>
			<CardContent className="space-y-4">
				<p className="text-sm text-muted-foreground">
					For a NAT'd / edge instance the panel can't reach. Mint a one-time
					join code and run the printed command on the instance host — it
					connects outbound and self-registers.
				</p>

				<div className="flex flex-wrap items-end gap-3">
					<div className="flex flex-col gap-1">
						<label htmlFor="jc-name" className="text-xs text-muted-foreground">
							Name (optional)
						</label>
						<input
							id="jc-name"
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder="edge-oms"
							className={`${FIELD} w-48`}
						/>
					</div>
					<div className="flex flex-col gap-1">
						<label htmlFor="jc-tier" className="text-xs text-muted-foreground">
							Granted tier
						</label>
						<select
							id="jc-tier"
							value={tier}
							onChange={(e) => setTier(e.target.value)}
							className={FIELD}
						>
							{TIERS.map((t) => (
								<option key={t} value={t}>
									{t}
								</option>
							))}
						</select>
					</div>
					<Button onClick={mint} disabled={minting}>
						{minting ? "Minting…" : "Mint join code"}
					</Button>
				</div>

				{error ? <p className="text-sm text-destructive">{error}</p> : null}

				{result ? (
					<div className="space-y-3 rounded-md border border-warning/40 bg-warning/5 p-4">
						<p className="flex items-center gap-2 text-sm font-medium text-warning">
							<TriangleAlert className="size-4" />
							One-time code — expires in {result.expires_in}s. Run this on the
							instance host:
						</p>
						<div className="flex items-center gap-2">
							<code className="flex-1 truncate rounded bg-muted px-2 py-1.5 font-mono text-xs">
								{connectCmd}
							</code>
							<CopyButton value={connectCmd} label="Copy command" />
						</div>
						<p className="text-xs text-muted-foreground">
							Grants tier <code>{result.tier}</code>. The instance appears here
							once it connects.
						</p>
					</div>
				) : null}
			</CardContent>
		</Card>
	);
}
