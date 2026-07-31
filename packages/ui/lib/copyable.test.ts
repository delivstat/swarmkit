import { copyText } from "@/components/copyable";
import { afterEach, describe, expect, it, vi } from "vitest";

// The clipboard path matters more than it looks: a workspace portal usually runs on
// http://127.0.0.1:8000 or a LAN host, and `navigator.clipboard` is unavailable on a NON-SECURE
// origin. Without the fallback the copy button would be dead exactly where the UI normally lives.

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

describe("copyText", () => {
	it("uses the async clipboard API when it is available", async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		vi.stubGlobal("navigator", { clipboard: { writeText } });
		expect(await copyText("artifact body")).toBe(true);
		expect(writeText).toHaveBeenCalledWith("artifact body");
	});

	it("falls back to execCommand on a non-secure origin, where clipboard is absent", async () => {
		vi.stubGlobal("navigator", {});
		const execCommand = vi.fn().mockReturnValue(true);
		vi.stubGlobal("document", {
			createElement: () => ({
				style: {},
				setAttribute: vi.fn(),
				select: vi.fn(),
				value: "",
			}),
			body: { appendChild: vi.fn(), removeChild: vi.fn() },
			execCommand,
		});
		expect(await copyText("artifact body")).toBe(true);
		expect(execCommand).toHaveBeenCalledWith("copy");
	});

	it("falls back when the clipboard API is present but rejects (denied permission)", async () => {
		vi.stubGlobal("navigator", {
			clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
		});
		const execCommand = vi.fn().mockReturnValue(true);
		vi.stubGlobal("document", {
			createElement: () => ({
				style: {},
				setAttribute: vi.fn(),
				select: vi.fn(),
				value: "",
			}),
			body: { appendChild: vi.fn(), removeChild: vi.fn() },
			execCommand,
		});
		expect(await copyText("x")).toBe(true);
		expect(execCommand).toHaveBeenCalled();
	});

	it("reports failure rather than pretending, when both paths fail", async () => {
		vi.stubGlobal("navigator", {});
		vi.stubGlobal("document", {
			createElement: () => {
				throw new Error("no DOM");
			},
			body: {},
		});
		expect(await copyText("x")).toBe(false);
	});
});
