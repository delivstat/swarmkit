"use client";

import { useEffect, useState } from "react";
import { api } from "./api";
import type { RefOptions } from "./schema-form";

/** Fetch the workspace's artifact ids (skills / archetypes / topologies / funnels / contracts) once,
 * to populate the schema-driven form's x-swarmkit-ref pickers. Best-effort: a failed fetch leaves
 * that type empty (e.g. a runtime that does not yet serve `/contracts` simply yields an empty
 * contract picker — so a stage's `locks` field falls back to no options rather than erroring). */
export function useRefOptions(): RefOptions {
	const [options, setOptions] = useState<RefOptions>({});
	useEffect(() => {
		Promise.all([
			api
				.skills()
				.then((s) => s.map((x) => x.id))
				.catch(() => [] as string[]),
			api.archetypes().catch(() => [] as string[]),
			api.topologies().catch(() => [] as string[]),
			api.funnels().catch(() => [] as string[]),
			api.contracts().catch(() => [] as string[]),
		]).then(([skill, archetype, topology, funnel, contract]) =>
			setOptions({ skill, archetype, topology, funnel, contract }),
		);
	}, []);
	return options;
}
