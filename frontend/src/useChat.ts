import { useCallback, useRef, useState } from "react";
import { streamChat, stripCitations, type ChatEvent, type Plan, type Source, type StageId } from "./api";

export interface StageState {
  status: "pending" | "active" | "done" | "failed";
  detail: string;
}

export interface UserMessage {
  id: string;
  role: "user";
  content: string;
}

export interface AssistantMessage {
  id: string;
  role: "assistant";
  text: string; // markdown with inline [n] citation markers
  thinking: string;
  stages: Record<StageId, StageState>;
  plan: Plan | null;
  sources: Source[];
  notices: string[];
  error: string | null;
  usage: { model: string; input_tokens: number; output_tokens: number } | null;
  streaming: boolean;
}

export type Message = UserMessage | AssistantMessage;

export const plainText = (m: AssistantMessage) => stripCitations(m.text);

const newStages = (): Record<StageId, StageState> => ({
  plan: { status: "pending", detail: "" },
  retrieve: { status: "pending", detail: "" },
  answer: { status: "pending", detail: "" },
});

/** Mark whichever pipeline stage was running as failed (on error or user abort). */
const failActive = (m: AssistantMessage): AssistantMessage => ({
  ...m,
  stages: Object.fromEntries(
    Object.entries(m.stages).map(([id, s]) => [id, s.status === "active" ? { ...s, status: "failed" } : s]),
  ) as Record<StageId, StageState>,
});

function reduce(m: AssistantMessage, e: ChatEvent): AssistantMessage {
  switch (e.event) {
    case "stage":
      return { ...m, stages: { ...m.stages, [e.data.id]: { status: e.data.status, detail: e.data.detail } } };
    case "plan":
      return { ...m, plan: e.data };
    case "sources":
      return { ...m, sources: e.data.sources };
    case "token":
      return { ...m, text: m.text + e.data.text };
    case "thinking":
      return { ...m, thinking: m.thinking + e.data.text };
    case "notice":
      return { ...m, notices: [...m.notices, e.data.message] };
    case "error":
      return { ...failActive(m), error: e.data.message, streaming: false };
    case "done":
      return { ...m, usage: e.data, streaming: false };
  }
}

export function useChat(docScope: string[] | null) {
  const [messages, setMessages] = useState<Message[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const busy = messages.some((m) => m.role === "assistant" && m.streaming);

  const update = (id: string, fn: (m: AssistantMessage) => AssistantMessage) =>
    setMessages((ms) => ms.map((m) => (m.id === id && m.role === "assistant" ? fn(m) : m)));

  const send = useCallback(
    async (content: string) => {
      const user: UserMessage = { id: crypto.randomUUID(), role: "user", content };
      const assistant: AssistantMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        text: "",
        thinking: "",
        stages: newStages(),
        plan: null,
        sources: [],
        notices: [],
        error: null,
        usage: null,
        streaming: true,
      };
      const history = [...messages, user]
        .filter((m) => m.role === "user" || !m.error)
        .map((m) => ({ role: m.role, content: m.role === "user" ? m.content : plainText(m) }));
      setMessages((ms) => [...ms, user, assistant]);

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamChat({ messages: history, doc_ids: docScope }, (e) => update(assistant.id, (m) => reduce(m, e)), controller.signal);
        update(assistant.id, (m) => ({ ...m, streaming: false }));
      } catch (err) {
        const aborted = controller.signal.aborted;
        update(assistant.id, (m) => ({
          ...failActive(m),
          streaming: false,
          error: aborted ? null : (err as Error).message,
          notices: aborted ? [...m.notices, "Stopped by user."] : m.notices,
        }));
      }
    },
    [messages, docScope],
  );

  const stop = () => abortRef.current?.abort();
  const reset = () => {
    stop();
    setMessages([]);
  };

  return { messages, send, stop, reset, busy };
}
