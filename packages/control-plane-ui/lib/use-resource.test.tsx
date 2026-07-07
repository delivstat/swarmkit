import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it, vi } from "vitest";

import { useResource } from "./use-resource";

/** Each test gets a fresh, isolated SWR cache so keys don't leak between cases. */
function Wrapper({ children }: { children: React.ReactNode }) {
	return (
		<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
			{children}
		</SWRConfig>
	);
}

function Probe<T>({
	rkey,
	fetcher,
	render: renderData,
}: {
	rkey: string | null;
	fetcher: () => Promise<T>;
	render?: (r: ReturnType<typeof useResource<T>>) => React.ReactNode;
}) {
	const r = useResource<T>(rkey, fetcher, { refreshInterval: 0 });
	if (renderData) return <>{renderData(r)}</>;
	return (
		<div>
			<span data-testid="loading">{String(r.loading)}</span>
			<span data-testid="error">{r.error ?? ""}</span>
			<span data-testid="data">{r.data ? JSON.stringify(r.data) : ""}</span>
		</div>
	);
}

describe("useResource", () => {
	it("loads then exposes data (loading resolves to false)", async () => {
		const fetcher = vi.fn(async () => ({ ok: 1 }));
		render(
			<Wrapper>
				<Probe rkey="/thing" fetcher={fetcher} />
			</Wrapper>,
		);
		expect(screen.getByTestId("loading").textContent).toBe("true");
		await waitFor(() =>
			expect(screen.getByTestId("data").textContent).toBe('{"ok":1}'),
		);
		expect(screen.getByTestId("loading").textContent).toBe("false");
		expect(screen.getByTestId("error").textContent).toBe("");
	});

	it("dedupes concurrent fetches of the same key across components", async () => {
		const fetcher = vi.fn(async () => [1, 2, 3]);
		render(
			<Wrapper>
				<Probe rkey="/shared" fetcher={fetcher} />
				<Probe rkey="/shared" fetcher={fetcher} />
			</Wrapper>,
		);
		await waitFor(() =>
			expect(screen.getAllByTestId("data")[0]?.textContent).toBe("[1,2,3]"),
		);
		// Two components, one key → a single network call (the usePoll bug this fixes).
		expect(fetcher).toHaveBeenCalledTimes(1);
	});

	it("surfaces a fetch error as a string message", async () => {
		const fetcher = vi.fn(async () => {
			throw new Error("boom");
		});
		render(
			<Wrapper>
				<Probe rkey="/bad" fetcher={fetcher} />
			</Wrapper>,
		);
		await waitFor(() =>
			expect(screen.getByTestId("error").textContent).toBe("boom"),
		);
	});

	it("does not fetch when the key is null (conditional/disabled)", async () => {
		const fetcher = vi.fn(async () => "x");
		render(
			<Wrapper>
				<Probe rkey={null} fetcher={fetcher} />
			</Wrapper>,
		);
		await waitFor(() =>
			expect(screen.getByTestId("loading").textContent).toBe("false"),
		);
		expect(fetcher).not.toHaveBeenCalled();
		expect(screen.getByTestId("data").textContent).toBe("");
	});

	it("refresh() revalidates the resource", async () => {
		let n = 0;
		const fetcher = vi.fn(async () => ++n);
		render(
			<Wrapper>
				<Probe<number>
					rkey="/counter"
					fetcher={fetcher}
					render={(r) => (
						<div>
							<span data-testid="val">{r.data ?? ""}</span>
							<button type="button" onClick={r.refresh}>
								refresh
							</button>
						</div>
					)}
				/>
			</Wrapper>,
		);
		await waitFor(() =>
			expect(screen.getByTestId("val").textContent).toBe("1"),
		);
		fireEvent.click(screen.getByText("refresh"));
		await waitFor(() =>
			expect(screen.getByTestId("val").textContent).toBe("2"),
		);
	});
});
