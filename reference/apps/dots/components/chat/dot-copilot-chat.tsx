"use client";

import { HttpAgent } from "@ag-ui/client";
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";
import type { Dot } from "@/lib/dots.config";
import { useMemo } from "react";

// Wrap each Dot in a CopilotKit provider whose "self-managed agent" is an AG-UI HttpAgent
// pointed at this app's /api/ag-ui proxy (which forwards to swarmkit serve's /api/ag-ui/run).
//
// `selfManagedAgents` is CopilotKit v1.77's sanctioned way to drive <CopilotChat> from an
// AG-UI endpoint without going through CopilotCloud or a GraphQL runtime. The map key is the
// agentId; we use the Dot id so each Dot gets its own thread history.
//
// The proxy carries the dotId as a query param; the server-side route maps it to the Dot's
// topology before forwarding (see app/api/ag-ui/route.ts).

export function DotCopilotChat({ dot }: { dot: Dot }): React.ReactElement {
	// We mount a fresh <CopilotKit> provider per Dot and register its HttpAgent under the
	// "default" key. CopilotChat (v1 UI) uses the implicit default agentId; the per-Dot provider
	// keeps threads scoped to this surface.
	// HttpAgent's agentId is used by CopilotKit as the map key regardless of what we pass to
	// selfManagedAgents, so we fix it to "default" (matching CopilotChat's implicit lookup) and
	// let the per-Dot URL + threadId carry the Dot identity server-side.
	const agent = useMemo(
		() =>
			new HttpAgent({
				agentId: "default",
				url: `/api/ag-ui?dotId=${encodeURIComponent(dot.id)}`,
				threadId: `${dot.id}:default`,
			}),
		[dot.id],
	);
	return (
		<CopilotKit key={dot.id} selfManagedAgents={{ default: agent }} showDevConsole={false}>
			<div className="flex min-h-0 flex-1 flex-col">
				<CopilotChat instructions={dot.role} labels={{ title: dot.name, initial: dot.greeting }} />
			</div>
		</CopilotKit>
	);
}
