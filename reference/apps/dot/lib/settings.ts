// The user-facing shape the Settings page reads and writes. DOT translates this into a
// workspace-config patch when saving (design/details/dot-app.md §3.5 Settings).

export interface Settings {
	briefTime: string; // HH:MM local
	briefTimezone: string; // IANA zone name
	modelProvider: string;
	modelName: string;
	rankerItemCap: number;
}

export const MODEL_PROVIDERS = [
	{ id: "ollama", label: "Ollama (local)" },
	{ id: "openrouter", label: "OpenRouter" },
	{ id: "anthropic", label: "Anthropic" },
	{ id: "openai", label: "OpenAI" },
	{ id: "google", label: "Google" },
] as const;

// Shape of the workspace-config surface as DOT understands it. The runtime's real endpoint may
// carry more fields; DOT reads and writes only these.
export interface WorkspaceConfigProjection {
	triggers?: { "morning-brief"?: { time?: string; timezone?: string } };
	defaults?: { model?: { provider?: string; name?: string } };
	topologies?: { "morning-brief"?: { input?: { item_cap?: number } } };
}

export function toSettings(cfg: WorkspaceConfigProjection): Settings {
	return {
		briefTime: cfg.triggers?.["morning-brief"]?.time ?? "07:30",
		briefTimezone: cfg.triggers?.["morning-brief"]?.timezone ?? "UTC",
		modelProvider: cfg.defaults?.model?.provider ?? "ollama",
		modelName: cfg.defaults?.model?.name ?? "llama3.1:8b",
		rankerItemCap: cfg.topologies?.["morning-brief"]?.input?.item_cap ?? 10,
	};
}

export function toPatch(s: Partial<Settings>): WorkspaceConfigProjection {
	const patch: WorkspaceConfigProjection = {};
	if (s.briefTime !== undefined || s.briefTimezone !== undefined) {
		const brief: { time?: string; timezone?: string } = {};
		if (s.briefTime !== undefined) brief.time = s.briefTime;
		if (s.briefTimezone !== undefined) brief.timezone = s.briefTimezone;
		patch.triggers = { "morning-brief": brief };
	}
	if (s.modelProvider !== undefined || s.modelName !== undefined) {
		const model: { provider?: string; name?: string } = {};
		if (s.modelProvider !== undefined) model.provider = s.modelProvider;
		if (s.modelName !== undefined) model.name = s.modelName;
		patch.defaults = { model };
	}
	if (s.rankerItemCap !== undefined) {
		patch.topologies = {
			"morning-brief": { input: { item_cap: s.rankerItemCap } },
		};
	}
	return patch;
}
