import { useEffect, useRef } from "react";
import { ArrowUp, KeyRound, Lock, Square } from "lucide-react";

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  busy,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  busy: boolean;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 220)}px`;
  }, [value]);

  useEffect(() => ref.current?.focus(), []);

  const canSend = value.trim().length > 1 && !busy && !disabled;
  return (
    <div className="composer-dock">
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSend) onSubmit();
        }}
      >
        <textarea
          ref={ref}
          value={value}
          rows={1}
          maxLength={4000}
          placeholder="Ask about Kubernetes…"
          aria-label="Question"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (canSend) onSubmit();
            }
          }}
        />
        {busy ? (
          <button type="button" className="send stop" onClick={onStop} aria-label="Stop generating">
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button type="submit" className="send" disabled={!canSend} aria-label="Send">
            <ArrowUp size={18} strokeWidth={2.4} />
          </button>
        )}
      </form>
      <p className="composer-hint">
        Answers are generated from retrieved kubernetes.io pages and may be wrong — verify commands via the cited
        sources.
      </p>
    </div>
  );
}

/** Shown instead of the composer while a demo visitor browses without a key. */
export function LockedComposer({ text, action, onUnlock }: { text: string; action: string; onUnlock: () => void }) {
  return (
    <div className="composer-dock">
      <div className="locked-bar">
        <Lock size={16} />
        <span>{text}</span>
        <button className="primary-button" onClick={onUnlock}>
          <KeyRound size={15} /> {action}
        </button>
      </div>
    </div>
  );
}
