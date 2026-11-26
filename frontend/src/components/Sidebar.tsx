import {
  BarChart3,
  Database,
  KeyRound,
  LogOut,
  MessageSquare,
  Monitor,
  Moon,
  Plus,
  Sun,
  Trash2,
  X,
} from "lucide-react";
import type { Conversation, DemoSession, Health, IndexStatus, Strategy } from "../types";

export type View = "chat" | "eval";
export type Theme = "system" | "light" | "dark";

const THEMES: { value: Theme; icon: typeof Sun; label: string }[] = [
  { value: "system", icon: Monitor, label: "System theme" },
  { value: "light", icon: Sun, label: "Light theme" },
  { value: "dark", icon: Moon, label: "Dark theme" },
];

export function Brand() {
  return (
    <div className="brand">
      <img src="/favicon.svg" alt="" width={26} height={26} />
      <div>
        <strong>Kubernetes Docs Assistant</strong>
        <span>Hybrid RAG · kubernetes.io</span>
      </div>
    </div>
  );
}

export function Sidebar({
  open,
  onClose,
  view,
  onView,
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  index,
  health,
  online,
  theme,
  onTheme,
  locked,
  session,
  onForgetKey,
}: {
  open: boolean;
  onClose: () => void;
  view: View;
  onView: (view: View) => void;
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  index: Record<Strategy, IndexStatus> | null;
  health: Health | null;
  online: boolean | null;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  /** Demo without a key: read-only example instead of history, key entry instead of "New chat". */
  locked?: { label: string; sectionLabel: string; onUnlock: () => void };
  session?: DemoSession | null;
  onForgetKey?: () => void;
}) {
  return (
    <>
      <div className={`scrim ${open ? "show" : ""}`} onClick={onClose} aria-hidden />
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="sidebar-top">
          <Brand />
          <button className="icon-button mobile-only" onClick={onClose} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>

        {locked ? (
          <button className="new-chat" onClick={locked.onUnlock}>
            <KeyRound size={16} /> {locked.label}
          </button>
        ) : (
          <button className="new-chat" onClick={onNew}>
            <Plus size={16} /> New chat
            <kbd>⌘K</kbd>
          </button>
        )}

        <nav className="nav">
          <button className={view === "chat" ? "on" : ""} onClick={() => onView("chat")}>
            <MessageSquare size={16} /> Chat
          </button>
          <button className={view === "eval" ? "on" : ""} onClick={() => onView("eval")}>
            <BarChart3 size={16} /> Evaluation
          </button>
        </nav>

        <div className="history">
          <div className="section-label">{locked ? locked.sectionLabel : "Recent"}</div>
          {conversations.length === 0 && <p className="muted small pad">No conversations yet.</p>}
          <ul>
            {conversations.map((item) => (
              <li key={item.id} className={item.id === activeId && view === "chat" ? "on" : ""}>
                <button className="history-item" onClick={() => onSelect(item.id)} title={item.title}>
                  {item.title}
                </button>
                {!locked && (
                  <button
                    className="icon-button delete"
                    onClick={() => onDelete(item.id)}
                    aria-label={`Delete “${item.title}”`}
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>

        <div className="sidebar-foot">
          <div className="status-card">
            <div className="status-line">
              <span className={`dot ${online ? "ok" : online === false ? "down" : ""}`} />
              {online === null ? "Connecting…" : online ? "Backend online" : "Backend unreachable"}
            </div>
            {index && (
              <div className="status-line muted">
                <Database size={13} />
                {index.window.pages.toLocaleString("en-US")} pages ·{" "}
                {(index.window.chunks + index.heading.chunks).toLocaleString("en-US")} chunks
              </div>
            )}
            {health && <div className="status-line muted mono small">{health.chat_model}</div>}
          </div>
          {session?.enabled && (
            <div className="status-card demo-card">
              <div className="status-line">
                <KeyRound size={13} /> Demo access
                {onForgetKey && (
                  <button
                    className="icon-button forget"
                    onClick={onForgetKey}
                    aria-label="Forget key"
                    title="Forget key"
                  >
                    <LogOut size={13} />
                  </button>
                )}
              </div>
              <div className="status-line muted small">
                {session.remaining_today} of {session.daily_limit} questions left today
              </div>
              {session.expires_at && (
                <div className="status-line muted small">
                  Key valid until{" "}
                  {new Date(session.expires_at).toLocaleDateString("en-GB", {
                    day: "numeric",
                    month: "short",
                    year: "numeric",
                  })}
                </div>
              )}
            </div>
          )}
          <div className="theme-switch" role="radiogroup" aria-label="Theme">
            {THEMES.map(({ value, icon: Icon, label }) => (
              <button
                key={value}
                role="radio"
                aria-checked={theme === value}
                aria-label={label}
                title={label}
                className={theme === value ? "on" : ""}
                onClick={() => onTheme(value)}
              >
                <Icon size={14} />
              </button>
            ))}
          </div>
        </div>
      </aside>
    </>
  );
}
