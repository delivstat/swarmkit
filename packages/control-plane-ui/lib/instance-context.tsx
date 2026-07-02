"use client";

import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useState,
} from "react";

import { api } from "@/lib/api";
import type { Instance } from "@/lib/types";
import { usePoll } from "@/lib/use-poll";

const STORAGE_KEY = "swarmkit.selectedInstance";

interface InstanceContextValue {
	instances: Instance[];
	selectedId: string;
	selected: Instance | null;
	setSelectedId: (id: string) => void;
	loading: boolean;
}

const InstanceContext = createContext<InstanceContextValue | null>(null);

/** Fleet-wide instance selection. Fetches the registry once and remembers the
 * operator's choice (localStorage) so per-instance pages share one selection. */
export function InstanceProvider({ children }: { children: React.ReactNode }) {
	const fetcher = useCallback(() => api.listInstances(), []);
	const { data, loading } = usePoll<Instance[]>(fetcher, 30_000);
	const instances = useMemo(() => data ?? [], [data]);

	const [selectedId, setSelectedIdState] = useState("");

	// Restore the persisted selection on mount (client-only).
	useEffect(() => {
		const saved = window.localStorage.getItem(STORAGE_KEY);
		if (saved) setSelectedIdState(saved);
	}, []);

	// Keep the selection valid: default to the first instance when unset or when the
	// remembered instance is no longer registered.
	useEffect(() => {
		if (instances.length === 0) return;
		const stillThere = instances.some((i) => i.id === selectedId);
		if (!selectedId || !stillThere) {
			setSelectedIdState(instances[0]?.id ?? "");
		}
	}, [instances, selectedId]);

	const setSelectedId = useCallback((id: string) => {
		setSelectedIdState(id);
		window.localStorage.setItem(STORAGE_KEY, id);
	}, []);

	const value = useMemo<InstanceContextValue>(
		() => ({
			instances,
			selectedId,
			selected: instances.find((i) => i.id === selectedId) ?? null,
			setSelectedId,
			loading,
		}),
		[instances, selectedId, setSelectedId, loading],
	);

	return <InstanceContext value={value}>{children}</InstanceContext>;
}

export function useInstances(): InstanceContextValue {
	const ctx = useContext(InstanceContext);
	if (!ctx) {
		throw new Error("useInstances must be used within an InstanceProvider");
	}
	return ctx;
}
