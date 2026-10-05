import { headers } from "next/headers";
import Link from "next/link";

interface ByModel {
	model: string;
	input_tokens?: number;
	output_tokens?: number;
	cost_usd?: number;
	runs?: number;
}

interface UsagePayload {
	summary?: {
		input_tokens?: number;
		output_tokens?: number;
		cost_usd?: number;
		runs?: number;
	};
	by_model?: ByModel[];
}

async function loadUsage(): Promise<UsagePayload> {
	const h = await headers();
	const host = h.get("host") ?? "127.0.0.1:3500";
	const protocol = h.get("x-forwarded-proto") ?? "http";
	try {
		const res = await fetch(`${protocol}://${host}/api/usage`, {
			cache: "no-store",
		});
		if (!res.ok) return { summary: {}, by_model: [] };
		return (await res.json()) as UsagePayload;
	} catch {
		return { summary: {}, by_model: [] };
	}
}

function tok(n?: number): string {
	if (!n) return "0";
	if (n > 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
	if (n > 1_000) return `${(n / 1_000).toFixed(1)}k`;
	return `${n}`;
}

function usd(n?: number): string {
	if (!n) return "$0.00";
	return `$${n.toFixed(2)}`;
}

export default async function UsagePage(): Promise<React.ReactElement> {
	const { summary, by_model = [] } = await loadUsage();
	const total = summary ?? {};
	return (
		<main className="mx-auto max-w-screen-md px-6 py-10">
			<Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
				← Dots
			</Link>
			<h1 className="mt-4 text-2xl font-semibold tracking-tight">Usage & cost</h1>
			<p className="mt-1 text-sm text-muted-foreground">
				Model calls each Dot&apos;s topology made through the SwarmKit runtime, totalled from runs
				recorded in the governance store.
			</p>
			<div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
				<Card label="Runs" value={`${total.runs ?? 0}`} />
				<Card label="Input tokens" value={tok(total.input_tokens)} />
				<Card label="Output tokens" value={tok(total.output_tokens)} />
				<Card label="Cost" value={usd(total.cost_usd)} />
			</div>
			<h2 className="mt-10 text-lg font-semibold tracking-tight">By model</h2>
			{by_model.length === 0 ? (
				<div className="mt-4 rounded-lg border border-border bg-muted/20 p-6 text-sm text-muted-foreground">
					No usage recorded. The mock runtime does not record usage; point <code>SWARMKIT_URL</code>{" "}
					at a live <code>swarmkit serve</code> to see per-model breakdowns here.
				</div>
			) : (
				<div className="mt-4 overflow-hidden rounded-lg border border-border">
					<table className="w-full text-sm">
						<thead className="bg-muted/30 text-left text-xs uppercase tracking-wider text-muted-foreground">
							<tr>
								<th className="px-4 py-2">Model</th>
								<th className="px-4 py-2 text-right">Runs</th>
								<th className="px-4 py-2 text-right">Input</th>
								<th className="px-4 py-2 text-right">Output</th>
								<th className="px-4 py-2 text-right">Cost</th>
							</tr>
						</thead>
						<tbody>
							{by_model.map((row) => (
								<tr key={row.model} className="border-t border-border">
									<td className="px-4 py-2 font-medium">{row.model}</td>
									<td className="px-4 py-2 text-right">{row.runs ?? 0}</td>
									<td className="px-4 py-2 text-right">{tok(row.input_tokens)}</td>
									<td className="px-4 py-2 text-right">{tok(row.output_tokens)}</td>
									<td className="px-4 py-2 text-right">{usd(row.cost_usd)}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
		</main>
	);
}

function Card({
	label,
	value,
}: {
	label: string;
	value: string;
}): React.ReactElement {
	return (
		<div className="rounded-lg border border-border bg-muted/10 p-4">
			<p className="text-xs uppercase tracking-wider text-muted-foreground">{label}</p>
			<p className="mt-1 text-2xl font-semibold">{value}</p>
		</div>
	);
}
