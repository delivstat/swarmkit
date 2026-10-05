import { promises as fs } from "node:fs";
import { loadTemplates, substitute, writeTopologyYaml } from "@/lib/author-templates";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
	vi.restoreAllMocks();
});

describe("substitute", () => {
	it("fills {{placeholders}} with values and leaves unknown ones blank", () => {
		const out = substitute("hello {{name}}, repo={{repo}}, missing={{nope}}", {
			name: "world",
			repo: "org/repo",
		});
		expect(out).toBe("hello world, repo=org/repo, missing=");
	});

	it("tolerates whitespace inside the braces", () => {
		expect(substitute("{{ a }} and {{b}}", { a: "1", b: "2" })).toBe("1 and 2");
	});
});

describe("loadTemplates", () => {
	it("reads both shipped templates from disk and parses their manifests", async () => {
		const templates = await loadTemplates();
		const ids = templates.map((t) => t.id).sort();
		expect(ids).toEqual(["daily-brief", "github-triage"]);

		const brief = templates.find((t) => t.id === "daily-brief");
		expect(brief).toBeDefined();
		if (!brief) return;
		expect(brief.params.map((p) => p.key)).toEqual(["sources", "when"]);
		expect(brief.topologyTemplate).toContain("{{dot_id}}");
		expect(brief.topologyTemplate).toContain("{{sources}}");
		expect(brief.defaultIcon).toBe("sunrise");
	});
});

describe("writeTopologyYaml", () => {
	it("writes to workspace/topologies/<id>.yaml", async () => {
		const captured: Array<{ file: string; data: string }> = [];
		vi.spyOn(fs, "mkdir").mockResolvedValue(undefined);
		vi.spyOn(fs, "writeFile").mockImplementation(async (file, data) => {
			captured.push({ file: String(file), data: String(data) });
		});
		await writeTopologyYaml("my-dot", "yaml-body");
		expect(captured).toHaveLength(1);
		expect(captured[0]?.file).toMatch(/workspace\/topologies\/my-dot\.yaml$/);
		expect(captured[0]?.data).toBe("yaml-body");
	});
});
