import { useRef, useState } from "react";
import { api, type DocumentRecord } from "../api";

interface Props {
  docs: DocumentRecord[];
  scope: Set<string> | null; // null = all ready documents
  onScopeChange: (scope: Set<string> | null) => void;
  onChanged: () => void;
  maxUploadMb: number | null;
}

const fmtSize = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);

export function Sidebar({ docs, scope, onScopeChange, onChanged, maxUploadMb }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [pending, setPending] = useState<string[]>([]);

  // One file per request: serverless request bodies are capped, and each file is
  // parsed and indexed within its own upload request.
  const upload = async (files: File[]) => {
    if (!files.length) return;
    const errors: string[] = [];
    const limit = maxUploadMb ? maxUploadMb * 1024 * 1024 : Infinity;
    const accepted = files.filter((f) => {
      if (f.size <= limit) return true;
      errors.push(`${f.name} is ${fmtSize(f.size)}; the limit is ${maxUploadMb} MB.`);
      return false;
    });
    setUploadError(errors.join(" ") || null);
    setPending(accepted.map((f) => f.name));
    for (const f of accepted) {
      try {
        const [doc] = await api.upload([f]);
        if (doc?.status === "failed" && doc.error) errors.push(`${f.name}: ${doc.error}`);
      } catch (e) {
        errors.push(`${f.name}: ${(e as Error).message}`);
      }
      setPending((p) => p.filter((name) => name !== f.name));
      onChanged();
    }
    setUploadError(errors.join(" ") || null);
  };

  const toggle = (id: string) => {
    const ready = docs.filter((d) => d.status === "ready").map((d) => d.id);
    const next = new Set(scope ?? ready);
    next.has(id) ? next.delete(id) : next.add(id);
    onScopeChange(next.size === ready.length ? null : next);
  };

  const remove = async (id: string) => {
    await api.deleteDocument(id);
    if (scope?.has(id)) {
      const next = new Set(scope);
      next.delete(id);
      onScopeChange(next);
    }
    onChanged();
  };

  const readyCount = docs.filter((d) => d.status === "ready").length;

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" aria-hidden>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
            <path d="M14 3v5h5M9 13h6M9 17h4" />
          </svg>
        </div>
        <div>
          <div className="brand-name">DocuMind</div>
          <div className="brand-sub">Document Analyst</div>
        </div>
      </div>

      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          upload(Array.from(e.dataTransfer.files));
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      >
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
          <path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
        </svg>
        <div className="dz-title">{pending.length ? `Processing ${pending.length} file(s)…` : "Drop PDFs here"}</div>
        <div className="dz-sub">
          or click to browse · PDF, TXT, MD{maxUploadMb ? ` · up to ${maxUploadMb} MB` : ""}
        </div>
        <input
          ref={input}
          type="file"
          multiple
          accept=".pdf,.txt,.md"
          hidden
          onChange={(e) => {
            upload(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
      </div>
      {uploadError && <div className="inline-error">{uploadError}</div>}

      <div className="section-head">
        <span>Documents</span>
        <span className="muted">
          {scope ? `${scope.size} of ${readyCount} in scope` : `${readyCount} ready`}
        </span>
      </div>

      <ul className="doc-list">
        {pending.map((name) => (
          <li key={`pending-${name}`} className="doc parsing">
            <div className="doc-main">
              <div className="doc-text">
                <div className="doc-name" title={name}>{name}</div>
                <div className="doc-meta">Uploading, parsing and indexing…</div>
              </div>
            </div>
            <div className="progress indeterminate"><div /></div>
          </li>
        ))}
        {docs.length === 0 && pending.length === 0 && <li className="empty">No documents yet. Upload an annual report, 10-K or earnings deck to begin.</li>}
        {docs.map((d) => {
          const ready = d.status === "ready";
          const inScope = ready && (scope === null || scope.has(d.id));
          return (
            <li key={d.id} className={`doc ${d.status}`}>
              <label className="doc-main">
                <input type="checkbox" disabled={!ready} checked={inScope} onChange={() => toggle(d.id)} />
                <div className="doc-text">
                  <div className="doc-name" title={d.filename}>{d.filename}</div>
                  <div className="doc-meta">
                    {ready
                      ? `${d.pages} pages · ${d.chunks} passages · ${fmtSize(d.size_bytes)}`
                      : d.status === "failed"
                        ? d.error
                        : d.detail}
                  </div>
                </div>
              </label>
              <div className="doc-actions">
                {ready && (
                  <a className="icon-btn" href={api.fileUrl(d.id)} target="_blank" rel="noreferrer" title="Open file" aria-label="Open file">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg>
                  </a>
                )}
                <button className="icon-btn" onClick={() => remove(d.id)} title="Delete" aria-label={`Delete ${d.filename}`}>
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>
                </button>
              </div>
              {!ready && d.status !== "failed" && (
                <div className="progress" aria-label={`${Math.round(d.progress * 100)}%`}>
                  <div style={{ width: `${Math.max(4, d.progress * 100)}%` }} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
