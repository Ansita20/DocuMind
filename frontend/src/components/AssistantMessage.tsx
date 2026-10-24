import { memo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { citedSources, linkCitations, type StageId } from "../api";
import type { AssistantMessage as Msg } from "../useChat";

const STAGES: { id: StageId; label: string }[] = [
  { id: "plan", label: "Planner" },
  { id: "retrieve", label: "Retriever" },
  { id: "answer", label: "Analyst" },
];

interface Props {
  msg: Msg;
  onCite: (msgId: string, sourceIndex: number) => void;
  activeSource: number | null;
}

export const AssistantMessage = memo(function AssistantMessage({ msg, onCite, activeSource }: Props) {
  const [showThinking, setShowThinking] = useState(false);
  const answering = msg.streaming && msg.stages.answer.status === "active";

  const components: Components = {
    a: ({ href, children }) => {
      if (href?.startsWith("#cite-")) {
        const idx = Number(href.slice(6));
        return (
          <button
            className={`cite ${activeSource === idx ? "active" : ""}`}
            onClick={() => onCite(msg.id, idx)}
            title={msg.sources[idx] ? `${msg.sources[idx].filename}, p. ${msg.sources[idx].page}` : undefined}
          >
            {children}
          </button>
        );
      }
      return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
    },
  };

  const cited = citedSources(msg.text, msg.sources.length);

  return (
    <div className="msg assistant">
      <div className="trace">
        {STAGES.map(({ id, label }) => {
          const s = msg.stages[id];
          return (
            <div key={id} className={`step ${s.status}`}>
              <span className="dot" aria-hidden />
              <span className="step-label">{label}</span>
              {s.detail && <span className="step-detail">{s.detail}</span>}
            </div>
          );
        })}
      </div>

      {msg.plan && msg.plan.needs_retrieval && (
        <div className="queries">
          {msg.plan.sub_queries.map((q) => (
            <span key={q} className="chip">
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
              {q}
            </span>
          ))}
        </div>
      )}

      {msg.thinking && (
        <div className="thinking">
          <button className="thinking-toggle" onClick={() => setShowThinking((v) => !v)}>
            <span className={`caret ${showThinking ? "open" : ""}`}>▸</span>
            {answering && !msg.text ? "Reasoning…" : "Reasoning summary"}
          </button>
          {showThinking && <div className="thinking-body">{msg.thinking}</div>}
        </div>
      )}

      <div className={`answer ${answering ? "streaming" : ""}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
          {linkCitations(msg.text, msg.sources.length)}
        </ReactMarkdown>
      </div>

      {msg.notices.map((n, i) => (
        <div key={i} className="notice">{n}</div>
      ))}
      {msg.error && <div className="inline-error">{msg.error}</div>}

      {!msg.streaming && cited.length > 0 && (
        <div className="cited-list">
          {cited.map((i) => {
            const s = msg.sources[i];
            if (!s) return null;
            return (
              <button key={i} className={`cited-item ${activeSource === i ? "active" : ""}`} onClick={() => onCite(msg.id, i)}>
                <span className="cited-num">{i + 1}</span>
                <span className="cited-file">{s.filename}</span>
                <span className="muted">p. {s.page}</span>
              </button>
            );
          })}
        </div>
      )}

      {msg.usage && (
        <div className="usage">
          {msg.usage.model} · {msg.usage.input_tokens.toLocaleString()} in · {msg.usage.output_tokens.toLocaleString()} out
        </div>
      )}
    </div>
  );
});
