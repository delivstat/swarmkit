"use client";

import { MessageCircle, Plus, Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type {
	ConversationDetail,
	ConversationListItem,
	ConversationTurn,
	TraceData,
} from "@/lib/types";
import { usePoll } from "@/lib/use-poll";
import { cn } from "@/lib/utils";

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
		<div className="flex h-full w-64 shrink-0 flex-col border-r">
			<div className="border-b p-3">
				<Button type="button" className="w-full" onClick={onCreate}>
					<Plus size={14} /> New Chat
				</Button>
			</div>
			<div className="flex-1 space-y-1 overflow-y-auto p-2">
				{conversations.length === 0 && (
					<p className="p-3 text-center text-xs text-muted-foreground">
						No conversations yet
					</p>
				)}
				{conversations.map((conv) => (
					<button
						key={conv.id}
						type="button"
						onClick={() => onSelect(conv.id)}
						className={cn(
							"w-full rounded-md px-3 py-2 text-left text-sm transition-colors",
							activeId === conv.id
								? "bg-accent font-medium text-accent-foreground"
								: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
						)}
					>
						<div className="flex items-center gap-2">
							<MessageCircle size={12} />
							<span className="truncate">
								{conv.last_message || conv.topology}
							</span>
						</div>
						<div className="mt-0.5 pl-5 text-xs text-muted-foreground">
							{conv.turns} turns
						</div>
					</button>
				))}
			</div>
		</div>
	);
}

function MessageBubble({
	turn,
	onRetry,
}: {
	turn: ConversationTurn;
	onRetry?: () => void;
}) {
	const isHuman = turn.role === "human";
	const isError = !isHuman && turn.content.startsWith("Error:");
	return (
		<div className={cn("mb-4 flex", isHuman ? "justify-end" : "justify-start")}>
			<div
				className={cn(
					"max-w-[75%] rounded-2xl px-4 py-2.5 text-sm",
					isHuman
						? "rounded-br-sm bg-sky-600 text-white"
						: isError
							? "rounded-bl-sm border border-destructive bg-background"
							: "rounded-bl-sm bg-card",
				)}
			>
				{isHuman ? (
					<div className="whitespace-pre-wrap">{turn.content}</div>
				) : (
					<div className="prose prose-sm dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_pre]:bg-[var(--bg)] [&_pre]:p-2 [&_pre]:rounded [&_pre]:text-xs [&_code]:text-xs [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_code]:bg-[var(--bg)] [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0 [&_p]:my-1 [&_h1]:text-base [&_h2]:text-sm [&_h3]:text-sm [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:italic [&_blockquote]:opacity-80 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs [&_table]:my-2 [&_th]:border [&_th]:border-[var(--border)] [&_th]:px-2 [&_th]:py-1 [&_th]:bg-[var(--bg)] [&_th]:font-semibold [&_th]:text-left [&_td]:border [&_td]:border-[var(--border)] [&_td]:px-2 [&_td]:py-1">
						<Markdown remarkPlugins={[remarkGfm]}>{turn.content}</Markdown>
					</div>
				)}
				<div className="mt-1 flex items-center gap-2">
					{turn.timestamp && (
						<span className="text-xs opacity-60">
							{new Date(turn.timestamp).toLocaleTimeString()}
						</span>
					)}
					{!isHuman && turn.usage && (
						<span className="text-xs opacity-60">
							{turn.usage.total_tokens.toLocaleString()} tok
							{turn.usage.by_model &&
								Object.keys(turn.usage.by_model).length > 0 && (
									<>
										{" · "}
										{Object.entries(turn.usage.by_model)
											.map(
												([m, t]) =>
													`${m.split("/").pop()} ${(
														((t as Record<string, number>)?.input ?? 0) +
															((t as Record<string, number>)?.output ?? 0)
													).toLocaleString()}`,
											)
											.join(", ")}
									</>
								)}
						</span>
					)}
					{isError && onRetry && (
						<Button
							type="button"
							variant="destructive"
							size="sm"
							className="ml-auto h-6 px-2 text-xs"
							onClick={onRetry}
						>
							Retry
						</Button>
					)}
				</div>
			</div>
		</div>
	);
}

function ThinkingIndicator({ progressLines }: { progressLines: string[] }) {
	const [elapsed, setElapsed] = useState(0);
	const [expanded, setExpanded] = useState(false);

	useEffect(() => {
		const timer = setInterval(() => setElapsed((e) => e + 1), 1000);
		return () => clearInterval(timer);
	}, []);

	const latest =
		progressLines.length > 0 ? progressLines[progressLines.length - 1] : null;

	return (
		<div className="mb-4 flex justify-start">
			<div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-card px-4 py-3 text-sm">
				<div className="flex items-center gap-2">
					<span className="flex gap-1">
						<span
							className="size-1.5 animate-bounce rounded-full bg-sky-500"
							style={{ animationDelay: "0ms" }}
						/>
						<span
							className="size-1.5 animate-bounce rounded-full bg-sky-500"
							style={{ animationDelay: "150ms" }}
						/>
						<span
							className="size-1.5 animate-bounce rounded-full bg-sky-500"
							style={{ animationDelay: "300ms" }}
						/>
					</span>
					<span className="text-xs text-muted-foreground">{elapsed}s</span>
					{progressLines.length > 1 && (
						<button
							type="button"
							onClick={() => setExpanded((e) => !e)}
							className="ml-auto text-xs text-sky-500"
						>
							{expanded ? "hide log" : `${progressLines.length} steps`}
						</button>
					)}
				</div>
				<div className="mt-1.5 font-mono text-xs text-muted-foreground">
					{latest ?? "Starting agent…"}
				</div>
				{expanded && progressLines.length > 1 && (
					<div className="mt-2 max-h-32 space-y-0.5 overflow-y-auto border-t pt-2">
						{progressLines.slice(0, -1).map((line, i) => (
							<div
								key={`progress-${i}-${line.slice(0, 20)}`}
								className="font-mono text-xs opacity-50"
							>
								{line}
							</div>
						))}
					</div>
				)}
			</div>
		</div>
	);
}

interface RunEvent {
	event_type: string;
	agent_id: string;
	timestamp: string;
	duration_ms?: number;
	model?: string;
	tokens?: number;
}

function TracePanel({ trace }: { trace: TraceData }) {
	const [expanded, setExpanded] = useState(false);
	const totalToolCalls = trace.agent_steps.reduce(
		(sum, s) => sum + s.tool_calls.length,
		0,
	);
	if (totalToolCalls === 0) return null;

	return (
		<div className="mb-2 flex justify-start">
			<div className="max-w-[85%] rounded-lg border bg-background px-3 py-2 text-xs">
				<button
					type="button"
					onClick={() => setExpanded(!expanded)}
					className="flex w-full items-center gap-2 text-left text-muted-foreground"
				>
					<span>{expanded ? "▼" : "▶"}</span>
					<span className="font-medium">Tool calls ({totalToolCalls})</span>
					<span>{(trace.duration_ms / 1000).toFixed(1)}s</span>
					<span>{trace.llm_calls} LLM calls</span>
				</button>
				{expanded && (
					<div className="mt-2 space-y-2">
						{trace.agent_steps.map((step) =>
							step.tool_calls.map((tc) => (
								<div
									key={`${step.agent_id}-${tc.tool_name}-${tc.duration_ms}`}
									className="flex flex-col gap-0.5 border-t py-1"
								>
									<div className="flex items-center gap-2">
										<span className="rounded bg-card px-1.5 py-0.5 font-mono">
											{tc.tool_name}
										</span>
										<span className="text-muted-foreground">
											{(tc.duration_ms / 1000).toFixed(1)}s
										</span>
										<span className="text-muted-foreground">
											{tc.result_length > 1024
												? `${(tc.result_length / 1024).toFixed(1)}KB`
												: `${tc.result_length}B`}
										</span>
										{tc.error && (
											<span className="text-destructive">{tc.error}</span>
										)}
									</div>
									{Object.keys(tc.arguments).length > 0 && (
										<div className="pl-2 font-mono text-muted-foreground">
											{Object.entries(tc.arguments).map(([k, v]) => (
												<span key={k} className="mr-2">
													{k}=
													{typeof v === "string"
														? `"${v.length > 60 ? `${v.slice(0, 60)}...` : v}"`
														: JSON.stringify(v)}
												</span>
											))}
										</div>
									)}
								</div>
							)),
						)}
					</div>
				)}
			</div>
		</div>
	);
}

interface RunUsage {
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	by_model: Record<string, { input: number; output: number }>;
}

const EVENT_ICONS: Record<string, string> = {
	"agent.started": "🚀",
	"agent.completed": "✅",
	"agent.failed": "❌",
	"tool.called": "🔧",
	"tool.completed": "📦",
	"governance.evaluated": "🛡️",
	"drift.checked": "📊",
	"memory.saved": "💾",
	"memory.retrieved": "🧠",
	"mcp.connected": "🔌",
};

function EventsSummary({
	events,
	usage,
}: { events: RunEvent[]; usage: RunUsage | null }) {
	if (events.length === 0 && !usage) return null;

	return (
		<div className="mb-4 flex justify-start">
			<div className="max-w-[85%] rounded-lg border bg-background px-3 py-2 text-xs">
				<div className="mb-1.5 flex items-center gap-3 text-muted-foreground">
					<span className="font-medium">Run details</span>
					{usage && (
						<>
							<span>{usage.total_tokens.toLocaleString()} tokens</span>
							<span>
								({usage.input_tokens.toLocaleString()} in /{" "}
								{usage.output_tokens.toLocaleString()} out)
							</span>
						</>
					)}
				</div>
				{usage && Object.keys(usage.by_model).length > 0 && (
					<div className="mb-1.5 flex gap-3 border-b pb-1.5">
						{Object.entries(usage.by_model).map(([model, tokens]) => (
							<span key={model} className="flex items-center gap-1">
								<span className="rounded bg-card px-1 py-0.5">
									{model.split("/").pop()}
								</span>
								<span className="text-muted-foreground">
									{(
										((tokens as Record<string, number>)?.input ?? 0) +
										((tokens as Record<string, number>)?.output ?? 0)
									).toLocaleString()}
								</span>
							</span>
						))}
					</div>
				)}
				<div className="space-y-0.5">
					{events.map((e) => (
						<div
							key={`${e.agent_id}-${e.event_type}-${e.timestamp}`}
							className="flex items-center gap-2 py-0.5"
						>
							<span>{EVENT_ICONS[e.event_type] ?? "•"}</span>
							<span className="font-medium">{e.agent_id}</span>
							<span className="text-muted-foreground">
								{e.event_type.replace("agent.", "").replace("tool.", "")}
							</span>
							{e.model && (
								<span className="rounded bg-card px-1 py-0.5 text-muted-foreground">
									{e.model}
								</span>
							)}
							{e.duration_ms != null && (
								<span className="text-muted-foreground">
									{(e.duration_ms / 1000).toFixed(1)}s
								</span>
							)}
							{e.tokens != null && (
								<span className="text-muted-foreground">
									{e.tokens.toLocaleString()} tok
								</span>
							)}
						</div>
					))}
				</div>
			</div>
		</div>
	);
}

function ChatArea({
	conversation,
	onSend,
	onRetry,
	sending,
	progressLines,
}: {
	conversation: ConversationDetail | null;
	onSend: (message: string) => void;
	onRetry: () => void;
	sending: boolean;
	progressLines: string[];
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
			<div className="flex flex-1 items-center justify-center">
				<div className="text-center text-muted-foreground">
					<MessageCircle size={48} className="mx-auto mb-3 opacity-30" />
					<p className="text-sm">Select a conversation or start a new one</p>
				</div>
			</div>
		);
	}

	return (
		<div className="flex h-full flex-1 flex-col">
			<div className="flex items-center gap-3 border-b px-4 py-3">
				<span className="font-medium">{conversation.topology}</span>
				<span className="rounded bg-card px-2 py-0.5 text-xs text-muted-foreground">
					{conversation.id}
				</span>
				<span className="ml-auto text-xs text-muted-foreground">
					{conversation.turns.length} turns
				</span>
			</div>

			<div className="flex-1 overflow-y-auto p-4">
				{conversation.turns.length === 0 && (
					<div className="py-12 text-center text-muted-foreground">
						<p className="text-sm">Start the conversation…</p>
					</div>
				)}
				{conversation.turns.map((turn, idx) => (
					<div key={`${turn.role}-${turn.timestamp}`}>
						<MessageBubble
							turn={turn}
							onRetry={
								idx === conversation.turns.length - 1 &&
								turn.role === "swarm" &&
								turn.content.startsWith("Error:")
									? onRetry
									: undefined
							}
						/>
						{turn.trace && <TracePanel trace={turn.trace} />}
					</div>
				))}
				{sending && <ThinkingIndicator progressLines={progressLines} />}
				<div ref={bottomRef} />
			</div>

			<div className="border-t p-3">
				<div className="flex gap-2">
					<Textarea
						className="flex-1 resize-none"
						rows={2}
						placeholder="Type a message… (Enter to send, Shift+Enter for newline)"
						value={input}
						onChange={(e) => setInput(e.target.value)}
						onKeyDown={handleKeyDown}
						disabled={sending}
					/>
					<Button
						type="button"
						size="icon"
						className="h-auto self-stretch"
						onClick={handleSend}
						disabled={sending || !input.trim()}
					>
						<Send size={20} />
					</Button>
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
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>New Conversation</DialogTitle>
				</DialogHeader>
				<div className="space-y-1.5">
					<Label htmlFor="topology-select">Topology</Label>
					<Select value={selected} onValueChange={setSelected}>
						<SelectTrigger id="topology-select">
							<SelectValue placeholder="Select topology…" />
						</SelectTrigger>
						<SelectContent>
							{topologies.map((t) => (
								<SelectItem key={t} value={t}>
									{t}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
				</div>
				<DialogFooter>
					<Button type="button" variant="outline" onClick={onClose}>
						Cancel
					</Button>
					<Button
						type="button"
						onClick={create}
						disabled={creating || !selected}
					>
						{creating ? "Creating…" : "Start Chat"}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
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
	const [progressLines, setProgressLines] = useState<string[]>([]);
	const [lastFailedMessage, setLastFailedMessage] = useState<string | null>(
		null,
	);

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
		setProgressLines([]);
		setLastFailedMessage(null);

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
			const result = await api.sendMessageStream(activeId, message, (text) =>
				setProgressLines((prev) => [...prev, text]),
			);
			const raw = result as unknown as {
				events?: RunEvent[];
				usage?: RunUsage;
				trace?: TraceData;
			};
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
									usage: raw.usage ?? undefined,
									events: raw.events ?? undefined,
									trace: raw.trace ?? undefined,
								},
							],
						}
					: null,
			);
			refetchConvs();
		} catch (err) {
			setLastFailedMessage(message);
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

	const handleRetry = () => {
		if (!lastFailedMessage || !activeConv) return;
		setActiveConv((prev) => {
			if (!prev) return null;
			const turns = prev.turns.slice(0, -2);
			return { ...prev, turns };
		});
		handleSend(lastFailedMessage);
	};

	const handleCreated = (id: string) => {
		setShowNew(false);
		refetchConvs();
		loadConversation(id);
	};

	return (
		<div className="-m-6 flex h-[calc(100vh-3rem)]">
			<ConversationList
				conversations={conversations ?? []}
				activeId={activeId}
				onSelect={loadConversation}
				onCreate={() => setShowNew(true)}
			/>
			<ChatArea
				conversation={activeConv}
				onSend={handleSend}
				onRetry={handleRetry}
				sending={sending}
				progressLines={progressLines}
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
