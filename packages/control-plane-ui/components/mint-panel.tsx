"use client";

import { KeyRound, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Instance, MintResult } from "@/lib/types";

const TIERS = ["read", "run", "admin"];

export function MintPanel({
	instance,
	onMinted,
}: {
	instance: Instance;
	onMinted: () => void;
}) {
	const [tier, setTier] = useState(instance.tier);
	const [minting, setMinting] = useState(false);
	const [result, setResult] = useState<MintResult | null>(null);
	const [error, setError] = useState<string | null>(null);

	async function mint() {
		setMinting(true);
		setError(null);
		try {
			setResult(await api.mintToken(instance.id, { tier }));
			onMinted();
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
		} finally {
			setMinting(false);
		}
	}

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2">
					<KeyRound className="size-4" />
					Token
				</CardTitle>
			</CardHeader>
			<CardContent className="space-y-4">
				<div className="flex flex-wrap items-center gap-3 text-sm">
					<span className="text-muted-foreground">
						{instance.token_fingerprint ? (
							<>
								Active fingerprint{" "}
								<code className="rounded bg-muted px-1 py-0.5 text-xs">
									{instance.token_fingerprint}
								</code>
								{instance.token_minted_at
									? ` · minted ${instance.token_minted_at}`
									: null}
							</>
						) : (
							"No token minted yet."
						)}
					</span>
				</div>

				<div className="flex items-center gap-2">
					<label htmlFor="mint-tier" className="text-sm text-muted-foreground">
						Tier
					</label>
					<select
						id="mint-tier"
						value={tier}
						onChange={(e) => setTier(e.target.value)}
						className="h-9 rounded-md border border-input bg-background px-2 text-sm"
					>
						{TIERS.map((t) => (
							<option key={t} value={t}>
								{t}
							</option>
						))}
					</select>
					<Button onClick={mint} disabled={minting}>
						{minting
							? "Minting…"
							: instance.token_fingerprint
								? "Rotate token"
								: "Mint token"}
					</Button>
				</div>

				{error ? <p className="text-sm text-destructive">{error}</p> : null}

				{result ? (
					<div className="space-y-3 rounded-md border border-warning/40 bg-warning/5 p-4">
						<p className="flex items-center gap-2 text-sm font-medium text-warning">
							<TriangleAlert className="size-4" />
							Shown once — copy it now. The panel stores only the fingerprint.
						</p>
						<div className="flex items-center gap-2">
							<code className="flex-1 truncate rounded bg-muted px-2 py-1.5 font-mono text-xs">
								{result.token}
							</code>
							<CopyButton value={result.token} label="Copy token" />
						</div>
						<div className="space-y-1">
							<div className="flex items-center justify-between">
								<span className="text-xs text-muted-foreground">
									server.auth snippet (paste on the instance · {result.key_ref})
								</span>
								<CopyButton
									value={result.server_auth_snippet}
									label="Copy YAML"
								/>
							</div>
							<pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
								{result.server_auth_snippet}
							</pre>
						</div>
						<p className="text-xs text-muted-foreground">
							{result.instructions}
						</p>
					</div>
				) : null}
			</CardContent>
		</Card>
	);
}
