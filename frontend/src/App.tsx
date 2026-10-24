import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type DocumentRecord, type Health } from "./api";
import { AssistantMessage } from "./components/AssistantMessage";
import { Sidebar } from "./components/Sidebar";
import { SourcesPanel } from "./components/SourcesPanel";
import { useChat, type AssistantMessage as AssistantMsg } from "./useChat";

const SUGGESTIONS = [
  "Summarize revenue, operating margin and net income trends across the reporting periods.",
  "What are the top risk factors management highlights, and how have they changed?",
  "Compare free cash flow to net income and explain the main differences.",
  "What guidance or outlook does management give for the next fiscal year?",
];

export default function App() {
  const [docs, setDocs] = useState<DocumentRecord[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [scope, setScope] = useState<Set<string> | null>(null);
  const [draft, setDraft] = useState("");
  const [panel, setPanel] = useState<{ msgId: string | null; source: number | null; open: boolean }>({
    msgId: null,
    source: null,
    open: false,
  });

  const scopeList = useMemo(() => (scope ? [...scope] : null), [scope]);
  const { messages, send, stop, reset, busy } = useChat(scopeList);

  const refreshDocs = useCallback(() => api.listDocuments().then(setDocs).catch(() => {}), []);

  useEffect(() => {
    refreshDocs();
    api.health().then(setHealth).catch(() => setHealth(null));
  }, [refreshDocs]);

  // Poll quickly while anything is ingesting; the backend pipeline runs in the background.
  const processing = docs.some((d) => d.status !== "ready" && d.status !== "failed");
  useEffect(() => {
    if (!processing) return;
    const t = setInterval(refreshDocs, 1000);
    return () => clearInterval(t);
  }, [processing, refreshDocs]);

  // The sources panel follows the latest answer unless the user picked an older one.
  const lastAssistant = [...messages].reverse().find((m): m is AssistantMsg => m.role === "assistant") ?? null;
  const panelMsg =
    (messages.find((m) => m.id === panel.msgId && m.role === "assistant") as AssistantMsg | undefined) ?? lastAssistant;
  useEffect(() => {
    if (lastAssistant?.sources.length) setPanel((p) => ({ ...p, msgId: lastAssistant.id, open: true }));
  }, [lastAssistant?.id, lastAssistant?.sources.length]);

  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const submit = (text = draft) => {
    const q = text.trim();
    if (!q || busy) return;
    setDraft("");
    setPanel((p) => ({ ...p, source: null }));
    send(q);
  };

  const readyCount = docs.filter((d) => d.status === "ready").length;

  return (
    <div className={`layout ${panel.open ? "with-sources" : ""}`}>
      <Sidebar docs={docs} scope={scope} onScopeChange={setScope} onChanged={refreshDocs} />

      <main className="chat">
        <header className="chat-head">
          <div>
            <h1>Analyst workspace</h1>
            <div className="muted small">
              {health ? (
                <>
                  <span className={`status-dot ${health.llm_configured ? "ok" : "warn"}`} />
                  {health.model}
                  {!health.llm_configured && " · no API key detected"}
                </>
              ) : (
                <>
                  <span className="status-dot bad" />
                  Backend offline
                </>
              )}
            </div>
          </div>
          <div className="head-actions">
            {messages.length > 0 && (
              <button className="ghost-btn" onClick={reset}>New chat</button>
            )}
            <button className="ghost-btn" onClick={() => setPanel((p) => ({ ...p, open: !p.open }))}>
              {panel.open ? "Hide sources" : "Sources"}
            </button>
          </div>
        </header>

        <div className="thread">
          {messages.length === 0 ? (
            <div className="welcome">
              <h2>Ask anything about your documents</h2>
              <p className="muted">
                A planner agent breaks your question into searches, a retriever pulls the most relevant passages, and an
                analyst agent answers with page-level citations, streamed live.
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="suggestion" onClick={() => submit(s)} disabled={busy}>
                    {s}
                  </button>
                ))}
              </div>
              {readyCount === 0 && <p className="muted small">Upload a document on the left to get grounded, cited answers.</p>}
            </div>
          ) : (
            messages.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="msg user">
                  <div className="bubble">{m.content}</div>
                </div>
              ) : (
                <AssistantMessage
                  key={m.id}
                  msg={m}
                  activeSource={panelMsg?.id === m.id ? panel.source : null}
                  onCite={(msgId, source) => setPanel({ msgId, source, open: true })}
                />
              ),
            )
          )}
          <div ref={endRef} />
        </div>

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={readyCount ? `Ask about ${scope ? scope.size : readyCount} document(s)…` : "Ask a question…"}
            rows={1}
          />
          {busy ? (
            <button type="button" className="send stop" onClick={stop} aria-label="Stop generating">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
            </button>
          ) : (
            <button type="submit" className="send" disabled={!draft.trim()} aria-label="Send">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          )}
        </form>
      </main>

      {panel.open && (
        <SourcesPanel
          msg={panelMsg}
          active={panel.source}
          onSelect={(source) => setPanel((p) => ({ ...p, msgId: panelMsg?.id ?? null, source }))}
          onClose={() => setPanel((p) => ({ ...p, open: false }))}
        />
      )}
    </div>
  );
}
