export type DocStatus = "queued" | "parsing" | "chunking" | "indexing" | "ready" | "failed";

export interface DocumentRecord {
  id: string;
  filename: string;
  size_bytes: number;
  status: DocStatus;
  progress: number;
  detail: string;
  pages: number;
  chunks: number;
  error: string | null;
  created_at: number;
}

export interface Source {
  index: number;
  chunk_id: string;
  doc_id: string;
  filename: string;
  page: number;
  text: string;
}

export interface Plan {
  needs_retrieval: boolean;
  sub_queries: string[];
  focus: string;
}

export type StageId = "plan" | "retrieve" | "answer";

export interface Health {
  ok: boolean;
  model: string;
  llm_configured: boolean;
  storage: "local" | "vercel-blob" | "missing";
  max_upload_mb: number;
}

/** Events emitted by POST /api/chat. Mirrors backend/app/agents.py. */
export type ChatEvent =
  | { event: "stage"; data: { id: StageId; status: "active" | "done"; detail: string } }
  | { event: "plan"; data: Plan }
  | { event: "sources"; data: { sources: Source[] } }
  | { event: "token"; data: { text: string } }
  | { event: "thinking"; data: { text: string } }
  | { event: "notice"; data: { message: string } }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: { model: string; input_tokens: number; output_tokens: number } };

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (res.status === 413) throw new Error("File is too large for the server's upload limit");
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
  }
  return res.json();
}

export const api = {
  health: () => fetch("/api/health").then((r) => json<Health>(r)),
  listDocuments: () => fetch("/api/documents").then((r) => json<DocumentRecord[]>(r)),
  deleteDocument: (id: string) => fetch(`/api/documents/${id}`, { method: "DELETE" }),
  fileUrl: (id: string, page?: number) => `/api/documents/${id}/file${page ? `#page=${page}` : ""}`,
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return fetch("/api/documents", { method: "POST", body: form }).then((r) => json<DocumentRecord[]>(r));
  },
};

/**
 * POST + Server-Sent Events. EventSource only supports GET, so we read the
 * fetch body stream and parse SSE frames ourselves.
 */
export async function streamChat(
  body: { messages: { role: "user" | "assistant"; content: string }[]; doc_ids: string[] | null },
  onEvent: (e: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Chat request failed (${res.status})`);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent({ event, data: JSON.parse(data.join("\n")) } as ChatEvent);
    }
  }
}

/** Inline citation markers the analyst writes: [3], [1][4] or [2, 5]. Not markdown links "[x](...)". */
const CITE_RE = /\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g;

/** Rewrite markers into links rendered as citation chips; numbers outside 1..sourceCount are left as-is. */
export function linkCitations(text: string, sourceCount: number): string {
  return text.replace(CITE_RE, (whole, nums: string) => {
    const ids = nums.split(",").map((n) => Number(n.trim()));
    if (ids.some((n) => n < 1 || n > sourceCount)) return whole;
    return ids.map((n) => `[${n}](#cite-${n - 1})`).join("");
  });
}

/** Zero-based source indexes cited anywhere in the text. */
export function citedSources(text: string, sourceCount: number): number[] {
  const out = new Set<number>();
  for (const m of text.matchAll(CITE_RE)) {
    for (const n of m[1].split(",").map((x) => Number(x.trim()))) {
      if (n >= 1 && n <= sourceCount) out.add(n - 1);
    }
  }
  return [...out].sort((a, b) => a - b);
}

/** Text without citation markers, for sending back as conversation history (passage numbers change per turn). */
export const stripCitations = (text: string) => text.replace(CITE_RE, "");
