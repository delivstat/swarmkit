"use client";

import { Card, CardTitle } from "@/components/card";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type {
	ConversationDetail,
	ConversationListItem,
	ConversationTurn,
} from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { MessageCircle, Plus, Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

function ConversationList({
	conversations,
	activeId,
	onSelect,
	onCreate,
}: {
	conversations: ConversationListItem[];
	activeId: string | null;
	onSelect: (id: string) => void;
	onCreate: () => void;
}) {
	return (
		<div
			className="w-64 shrink-0 border-r flex flex-col h-full"
			style={{ borderColor: "var(--border)" }}
		>
			<div className="p-3 border-b" style={{ borderColor: "var(--border)" }}>
				<button
					type="button"
					onClick={onCreate}
					className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded text-sm font-medium"
					style={{ background: "var(--accent)", color: "var(--accent-fg)" }}
				>
					<Plus size={14} />
					New Chat
				</button>
			</div>
			<div className="flex-1 overflow-y-auto p-2 space-y-1">
				{conversations.length === 0 && (
					<p
						className="text-xs p-3 text-center"
						style={{ color: "var(--fg-muted)" }}
					>
						No conversations yet
					</p>
				)}
				{conversations.map((conv) => (
					<button
						key={conv.id}
						type="button"
						onClick={() => onSelect(conv.id)}
						className={cn(
							"w-full text-left px-3 py-2 rounded text-sm transition-colors",
							activeId === conv.id
								? "font-medium"
								: "opacity-70 hover:opacity-100",
						)}
						style={activeId === conv.id ? { background: "var(--border)" } : {}}
					>
						<div className="flex items-center gap-2">
							<MessageCircle size={12} />
							<span className="truncate">
								{conv.last_message || conv.topology}
							</span>
						</div>
						<div
							className="text-xs mt-0.5 pl-5"
							style={{ color: "var(--fg-muted)" }}
						>
							{conv.turns} turns
						</div>
					</button>
				))}
			</div>
		</div>
	);
}

function MessageBubble({ turn }: { turn: ConversationTurn }) {
	const isHuman = turn.role === "human";
	return (
		<div className={cn("flex mb-4", isHuman ? "justify-end" : "justify-start")}>
			<div
				className={cn(
					"max-w-[75%] px-4 py-2.5 rounded-2xl text-sm",
					isHuman ? "rounded-br-sm" : "rounded-bl-sm",
				)}
				style={{
					background: isHuman ? "var(--accent)" : "var(--bg-sidebar)",
					color: isHuman ? "var(--accent-fg)" : "var(--fg)",
				}}
			>
				<div className="whitespace-pre-wrap">{turn.content}</div>
				{turn.timestamp && (
					<div className="text-xs mt-1 opacity-60">
						{new Date(turn.timestamp).toLocaleTimeString()}
					</div>
				)}
			</div>
		</div>
	);
}

function ChatArea({
	conversation,
	onSend,
	sending,
}: {
	conversation: ConversationDetail | null;
	onSend: (message: string) => void;
	sending: boolean;
}) {
	const [input, setInput] = useState("");
	const bottomRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		bottomRef.current?.scrollIntoView({ behavior: "smooth" });
	}, [conversation?.turns.length]);

	const handleSend = () => {
		if (!input.trim() || sending) return;
		onSend(input.trim());
		setInput("");
	};

	const handleKeyDown = (e: React.KeyboardEvent) => {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			handleSend();
		}
	};

	if (!conversation) {
		return (
			<div className="flex-1 flex items-center justify-center">
				<div className="text-center" style={{ color: "var(--fg-muted)" }}>
					<MessageCircle size={48} className="mx-auto mb-3 opacity-30" />
					<p className="text-sm">Select a conversation or start a new one</p>
				</div>
			</div>
		);
	}

	return (
		<div className="flex-1 flex flex-col h-full">
			<div
				className="px-4 py-3 border-b flex items-center gap-3"
				style={{ borderColor: "var(--border)" }}
			>
				<span className="font-medium">{conversation.topology}</span>
				<span
					className="text-xs px-2 py-0.5 rounded"
					style={{ background: "var(--bg-sidebar)", color: "var(--fg-muted)" }}
				>
					{conversation.id}
				</span>
				<span className="text-xs ml-auto" style={{ color: "var(--fg-muted)" }}>
					{conversation.turns.length} turns
				</span>
			</div>

			<div className="flex-1 overflow-y-auto p-4">
				{conversation.turns.length === 0 && (
					<div
						className="text-center py-12"
						style={{ color: "var(--fg-muted)" }}
					>
						<p className="text-sm">Start the conversation...</p>
					</div>
				)}
				{conversation.turns.map((turn) => (
					<MessageBubble key={`${turn.role}-${turn.timestamp}`} turn={turn} />
				))}
				{sending && (
					<div className="flex justify-start mb-4">
						<div
							className="px-4 py-2.5 rounded-2xl rounded-bl-sm text-sm"
							style={{ background: "var(--bg-sidebar)" }}
						>
							<span className="animate-pulse">Thinking...</span>
						</div>
					</div>
				)}
				<div ref={bottomRef} />
			</div>

			<div className="p-3 border-t" style={{ borderColor: "var(--border)" }}>
				<div className="flex gap-2">
					<textarea
						className="flex-1 px-3 py-2 rounded-lg border text-sm resize-none"
						style={{
							background: "var(--bg)",
							borderColor: "var(--border)",
							color: "var(--fg)",
						}}
						rows={2}
						placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
						value={input}
						onChange={(e) => setInput(e.target.value)}
						onKeyDown={handleKeyDown}
						disabled={sending}
					/>
					<button
						type="button"
						onClick={handleSend}
						disabled={sending || !input.trim()}
						className="px-3 rounded-lg self-end disabled:opacity-40"
						style={{ background: "var(--accent)", color: "var(--accent-fg)" }}
					>
						<Send size={16} />
					</button>
				</div>
			</div>
		</div>
	);
}

function NewChatDialog({
	topologies,
	onClose,
	onCreated,
}: {
	topologies: string[];
	onClose: () => void;
	onCreated: (id: string) => void;
}) {
	const [selected, setSelected] = useState(topologies[0] ?? "");
	const [creating, setCreating] = useState(false);

	const create = async () => {
		if (!selected) return;
		setCreating(true);
		try {
			const result = await api.createConversation(selected);
			onCreated(result.id);
		} catch {
			setCreating(false);
		}
	};

	return (
		<div
			className="fixed inset-0 flex items-center justify-center z-50"
			style={{ background: "rgba(0,0,0,0.5)" }}
		>
			<Card className="w-96">
				<CardTitle>New Conversation</CardTitle>
				<label
					htmlFor="topology-select"
					className="block text-sm mb-1"
					style={{ color: "var(--fg-muted)" }}
				>
					Topology
				</label>
				<select
					id="topology-select"
					className="w-full px-3 py-2 rounded border text-sm mb-4"
					style={{
						background: "var(--bg)",
						borderColor: "var(--border)",
						color: "var(--fg)",
					}}
					value={selected}
					onChange={(e) => setSelected(e.target.value)}
				>
					{topologies.map((t) => (
						<option key={t} value={t}>
							{t}
						</option>
					))}
				</select>
				<div className="flex gap-2 justify-end">
					<button
						type="button"
						onClick={onClose}
						className="px-3 py-1.5 text-sm rounded border"
						style={{ borderColor: "var(--border)" }}
					>
						Cancel
					</button>
					<button
						type="button"
						onClick={create}
						disabled={creating || !selected}
						className="px-3 py-1.5 text-sm rounded font-medium disabled:opacity-40"
						style={{ background: "var(--accent)", color: "var(--accent-fg)" }}
					>
						{creating ? "Creating..." : "Start Chat"}
					</button>
				</div>
			</Card>
		</div>
	);
}

export default function ChatPage() {
	const fetchConversations = useCallback(() => api.conversations(), []);
	const { data: conversations, refetch: refetchConvs } = usePoll<
		ConversationListItem[]
	>(fetchConversations, 10000);

	const fetchTopologies = useCallback(() => api.topologies(), []);
	const { data: topologies } = usePoll<string[]>(fetchTopologies, 30000);

	const [activeId, setActiveId] = useState<string | null>(null);
	const [activeConv, setActiveConv] = useState<ConversationDetail | null>(null);
	const [sending, setSending] = useState(false);
	const [showNew, setShowNew] = useState(false);

	const loadConversation = useCallback(async (id: string) => {
		setActiveId(id);
		try {
			const detail = await api.conversation(id);
			setActiveConv(detail);
		} catch {
			setActiveConv(null);
		}
	}, []);

	const handleSend = async (message: string) => {
		if (!activeId || !activeConv) return;
		setSending(true);

		setActiveConv((prev) =>
			prev
				? {
						...prev,
						turns: [
							...prev.turns,
							{
								role: "human" as const,
								content: message,
								timestamp: new Date().toISOString(),
							},
						],
					}
				: null,
		);

		try {
			const result = await api.sendMessage(activeId, message);
			setActiveConv((prev) =>
				prev
					? {
							...prev,
							turns: [
								...prev.turns,
								{
									role: "swarm" as const,
									content: result.output,
									timestamp: new Date().toISOString(),
								},
							],
						}
					: null,
			);
			refetchConvs();
		} catch (err) {
			setActiveConv((prev) =>
				prev
					? {
							...prev,
							turns: [
								...prev.turns,
								{
									role: "swarm" as const,
									content: `Error: ${err instanceof Error ? err.message : String(err)}`,
									timestamp: new Date().toISOString(),
								},
							],
						}
					: null,
			);
		} finally {
			setSending(false);
		}
	};

	const handleCreated = (id: string) => {
		setShowNew(false);
		refetchConvs();
		loadConversation(id);
	};

	return (
		<div className="flex h-[calc(100vh-3rem)] -m-6">
			<ConversationList
				conversations={conversations ?? []}
				activeId={activeId}
				onSelect={loadConversation}
				onCreate={() => setShowNew(true)}
			/>
			<ChatArea
				conversation={activeConv}
				onSend={handleSend}
				sending={sending}
			/>
			{showNew && topologies && (
				<NewChatDialog
					topologies={topologies}
					onClose={() => setShowNew(false)}
					onCreated={handleCreated}
				/>
			)}
		</div>
	);
}
