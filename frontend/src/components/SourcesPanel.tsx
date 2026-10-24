import { useEffect, useRef } from "react";
import { api, citedSources } from "../api";
import type { AssistantMessage } from "../useChat";

interface Props {
  msg: AssistantMessage | null;
  active: number | null;
  onSelect: (i: number) => void;
  onClose: () => void;
}

export function SourcesPanel({ msg, active, onSelect, onClose }: Props) {
  const activeRef = useRef<HTMLLIElement>(null);
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [active, msg?.id]);

  const sources = msg?.sources ?? [];
  const cited = new Set(msg ? citedSources(msg.text, msg.sources.length) : []);

  return (
    <aside className="sources">
      <div className="sources-head">
        <div>
          <div className="panel-title">Sources</div>
          <div className="muted small">
            {sources.length ? `${sources.length} retrieved · ${cited.size} cited` : "Retrieved passages appear here"}
          </div>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close sources">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </div>
      <ul className="source-list">
        {sources.map((s) => (
          <li
            key={s.chunk_id}
            ref={active === s.index ? activeRef : undefined}
            className={`source ${active === s.index ? "active" : ""} ${cited.has(s.index) ? "cited" : ""}`}
            onClick={() => onSelect(s.index)}
          >
            <div className="source-head">
              <span className="cited-num">{s.index + 1}</span>
              <span className="source-file" title={s.filename}>{s.filename}</span>
              <a
                className="page-link"
                href={api.fileUrl(s.doc_id, s.page)}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
              >
                p. {s.page} ↗
              </a>
            </div>
            <p className="source-text">{s.text}</p>
          </li>
        ))}
      </ul>
    </aside>
  );
}
