import { describe, expect, it } from "vitest";
import {
	type ContractDoc,
	type ContractSpec,
	contractLabel,
	partiesById,
	readContract,
} from "./contract";

/** A minimal valid-shaped contract document. */
function contract(over: Record<string, unknown> = {}): ContractDoc {
	return {
		apiVersion: "swarmkit/v1",
		kind: "Contract",
		metadata: {
			id: "oms-web",
			name: "OMS ↔ Web",
			description: "the order api",
		},
		parties: ["oms", "web"],
		provenance: { authored_by: "human", version: "1.0.0" },
		...over,
	};
}

describe("readContract", () => {
	it("projects id, name, parties, and interface", () => {
		const spec = readContract(
			contract({ interface: "https://schemas/oms.json" }),
		);
		expect(spec).toEqual<ContractSpec>({
			id: "oms-web",
			name: "OMS ↔ Web",
			parties: ["oms", "web"],
			interface: "https://schemas/oms.json",
		});
	});

	it("defaults missing/malformed fields to empty rather than throwing", () => {
		expect(readContract({})).toEqual<ContractSpec>({
			id: "",
			name: null,
			parties: [],
			interface: null,
		});
	});

	it("tolerates a null/undefined document", () => {
		expect(readContract(null).parties).toEqual([]);
		expect(readContract(undefined).id).toBe("");
	});

	it("drops non-string entries from parties", () => {
		const spec = readContract(contract({ parties: ["oms", 42, null, "web"] }));
		expect(spec.parties).toEqual(["oms", "web"]);
	});
});

describe("contractLabel", () => {
	it("joins two or more parties with the interface arrow", () => {
		expect(contractLabel("oms-web", ["oms", "web"])).toBe(
			"oms-web (oms ↔ web)",
		);
	});

	it("falls back to the id when fewer than two parties are known", () => {
		expect(contractLabel("oms-web", ["oms"])).toBe("oms-web");
		expect(contractLabel("oms-web", [])).toBe("oms-web");
	});
});

describe("partiesById", () => {
	it("indexes specs by id → parties, skipping id-less specs", () => {
		const specs: ContractSpec[] = [
			{ id: "a", name: null, parties: ["x", "y"], interface: null },
			{ id: "", name: null, parties: ["ignored"], interface: null },
			{ id: "b", name: null, parties: ["p", "q"], interface: null },
		];
		expect(partiesById(specs)).toEqual({ a: ["x", "y"], b: ["p", "q"] });
	});
});
