import { useState } from "react";
import { AlertCircle, Check, ChevronDown, Copy, RotateCcw, Square } from "lucide-react";
import type { AssistantMessage, Message } from "../types";
import { Markdown } from "./Markdown";
import { Sources } from "./Sources";
import { Inspector } from "./Inspector";
import { ms } from "../lib/format";

function Pending({ stage }: { stage: "retrieving" | "generating" }) {
  return (
    <div className="pending" role="status">
      <span className="dots" aria-hidden>
        <i />
        <i />
        <i />
      </span>
      {stage === "retrieving" ? "Searching the documentation…" : "Writing the answer…"}
    </div>
  );
}

function AssistantView({ message, onRetry }: { message: AssistantMessage; onRetry?: () => void }) {
  const [copied, setCopied] = useState(false);
  const [inspect, setInspect] = useState(false);
  const busy = message.status === "retrieving" || message.status === "generating";

  async function copy() {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <article className="msg assistant">
      <div className="avatar" aria-hidden>
        <svg viewBox="0 0 32 32" width="18" height="18">
          <path
            d="M9 10.5h8.5a3.5 3.5 0 0 1 3.5 3.5v8.5H12.5A3.5 3.5 0 0 1 9 19z"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.6"
            strokeLinejoin="round"
          />
          <circle cx="21.5" cy="21.5" r="3.4" fill="currentColor" />
        </svg>
      </div>
      <div className="msg-body">
        {message.standaloneQuestion && (
          <p className="rewritten">
            Searched for: <span>{message.standaloneQuestion}</span>
          </p>
        )}
        {message.content ? (
          <Markdown text={message.content} citations={message.citations} />
        ) : (
          busy && <Pending stage={message.status as "retrieving" | "generating"} />
        )}
        {message.status === "generating" && message.content && <span className="caret" aria-hidden />}
        {message.status === "error" && (
          <div className="notice error">
            <AlertCircle size={15} />
            <span>{message.error}</span>
          </div>
        )}
        {message.status === "stopped" && (
          <p className="muted small">
            <Square size={11} /> Generation stopped
          </p>
        )}
        <Sources citations={message.citations} />
        {!busy && (
          <div className="msg-actions">
            {message.content && (
              <button className="ghost-button" onClick={copy} aria-label="Copy answer">
                {copied ? <Check size={14} /> : <Copy size={14} />}
                {copied ? "Copied" : "Copy"}
              </button>
            )}
            {onRetry && (message.status === "error" || message.status === "stopped") && (
              <button className="ghost-button" onClick={onRetry}>
                <RotateCcw size={14} /> Retry
              </button>
            )}
            {message.trace && (
              <button
                className={`ghost-button ${inspect ? "on" : ""}`}
                onClick={() => setInspect((value) => !value)}
                aria-expanded={inspect}
              >
                <ChevronDown size={14} className="chev" /> Retrieval details
              </button>
            )}
            <span className="meta">
              {message.settings.strategy} · {message.trace?.rerank_applied ? "reranked" : "hybrid RRF"}
              {message.totalMs != null && ` · ${ms(message.totalMs)}`}
            </span>
          </div>
        )}
        {inspect && <Inspector message={message} />}
      </div>
    </article>
  );
}

export function MessageView({ message, onRetry }: { message: Message; onRetry?: () => void }) {
  if (message.role === "user") {
    return (
      <article className="msg user">
        <div className="bubble">{message.content}</div>
      </article>
    );
  }
  return <AssistantView message={message} onRetry={onRetry} />;
}
