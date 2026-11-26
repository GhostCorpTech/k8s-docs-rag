import { useEffect, useRef } from "react";
import { AlertTriangle, ArrowUpRight } from "lucide-react";
import type { Conversation } from "../types";
import { MessageView } from "./MessageView";

const EXAMPLES = [
  { text: "Чем Deployment отличается от StatefulSet?", note: "Workloads" },
  { text: "How do liveness, readiness and startup probes differ?", note: "Pod lifecycle" },
  { text: "Как работает Horizontal Pod Autoscaler?", note: "Autoscaling" },
  { text: "What happens to Pods during node-pressure eviction?", note: "Scheduling" },
];

export function EmptyState({ onPick, indexEmpty }: { onPick: (text: string) => void; indexEmpty?: boolean }) {
  return (
    <div className="empty">
      {indexEmpty && (
        <div className="notice warn">
          <AlertTriangle size={15} />
          <span>
            The index is empty. Build it with <code>python -m app.cli index --sync --strategy window</code> (see README
            → Quick start).
          </span>
        </div>
      )}
      <h1>Ask the Kubernetes documentation</h1>
      <p>
        Hybrid retrieval (vectors + BM25) over 1,730 pages of kubernetes.io. Every answer is grounded in the retrieved
        sections and cites them. Ask in English or Russian.
      </p>
      <div className="examples">
        {EXAMPLES.map((example) => (
          <button key={example.text} onClick={() => onPick(example.text)}>
            <span className="example-note">{example.note}</span>
            <span className="example-text">{example.text}</span>
            <ArrowUpRight size={15} className="example-icon" />
          </button>
        ))}
      </div>
    </div>
  );
}

export function ChatView({
  conversation,
  onPick,
  onRetry,
  indexEmpty,
  startAtTop,
}: {
  conversation: Conversation | null;
  onPick: (text: string) => void;
  onRetry?: () => void;
  indexEmpty?: boolean;
  /** Read-only example: open at the question, not at the end of the answer. */
  startAtTop?: boolean;
}) {
  const end = useRef<HTMLDivElement>(null);
  const messages = conversation?.messages ?? [];
  const last = messages.at(-1);
  const signature = last ? `${messages.length}:${last.content.length}` : "";

  useEffect(() => {
    const scroller = end.current?.closest(".scroll");
    if (scroller) scroller.scrollTop = startAtTop ? 0 : scroller.scrollHeight;
  }, [signature, startAtTop]);

  if (!messages.length) return <EmptyState onPick={onPick} indexEmpty={indexEmpty} />;
  return (
    <div className="thread">
      {messages.map((message, index) => (
        <MessageView key={message.id} message={message} onRetry={index === messages.length - 1 ? onRetry : undefined} />
      ))}
      <div ref={end} />
    </div>
  );
}
