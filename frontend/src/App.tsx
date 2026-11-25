import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Menu, SquarePen } from "lucide-react";
import { getHealth, getIndexStatus, streamChat, type ChatTurn } from "./api";
import { ChatView } from "./components/ChatView";
import { Composer } from "./components/Composer";
import { EvalView } from "./components/EvalView";
import { SettingsMenu } from "./components/SettingsMenu";
import { Brand, Sidebar, type Theme, type View } from "./components/Sidebar";
import { loadConversations, loadSettings, saveConversations, saveSettings, titleFrom, uid } from "./lib/store";
import type { AssistantMessage, Conversation, Health, IndexStatus, RetrievalSettings, Strategy } from "./types";

const HISTORY_MESSAGES = 6;

function readTheme(): Theme {
  try {
    const value = localStorage.getItem("theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function turnsOf(conversation: Conversation): ChatTurn[] {
  return conversation.messages
    .filter((message) => message.role === "user" || (message.status === "done" && message.content))
    .slice(-HISTORY_MESSAGES)
    .map((message) => ({ role: message.role, content: message.content.slice(0, 12_000) }));
}

export function App() {
  const [view, setView] = useState<View>(() => (location.hash === "#/eval" ? "eval" : "chat"));
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [settings, setSettings] = useState<RetrievalSettings>(loadSettings);
  const [busy, setBusy] = useState(false);
  const [sidebar, setSidebar] = useState(false);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [health, setHealth] = useState<Health | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [index, setIndex] = useState<Record<Strategy, IndexStatus> | null>(null);
  const abort = useRef<AbortController | null>(null);

  const active = useMemo(() => conversations.find((item) => item.id === activeId) ?? null, [conversations, activeId]);

  useEffect(() => {
    getHealth()
      .then((value) => {
        setHealth(value);
        setOnline(true);
      })
      .catch(() => setOnline(false));
    getIndexStatus()
      .then(setIndex)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!busy) saveConversations(conversations);
  }, [conversations, busy]);

  useEffect(() => saveSettings(settings), [settings]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try {
      if (theme === "system") localStorage.removeItem("theme");
      else localStorage.setItem("theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  useEffect(() => {
    window.history.replaceState(null, "", view === "eval" ? "#/eval" : "#/");
  }, [view]);

  const newChat = useCallback(() => {
    abort.current?.abort();
    setActiveId(null);
    setView("chat");
    setSidebar(false);
    setDraft("");
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        newChat();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newChat]);

  const patchAssistant = (
    conversationId: string,
    messageId: string,
    patch: (message: AssistantMessage) => Partial<AssistantMessage>,
  ) =>
    setConversations((items) =>
      items.map((conversation) =>
        conversation.id !== conversationId
          ? conversation
          : {
              ...conversation,
              updatedAt: Date.now(),
              messages: conversation.messages.map((message) =>
                message.id === messageId && message.role === "assistant" ? { ...message, ...patch(message) } : message,
              ),
            },
      ),
    );

  async function ask(question: string, base: Conversation | null) {
    const conversation: Conversation = base ?? {
      id: uid(),
      title: titleFrom(question),
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: [],
    };
    const priorTurns = turnsOf(conversation);
    const assistant: AssistantMessage = {
      role: "assistant",
      id: uid(),
      content: "",
      status: "retrieving",
      citations: [],
      settings: { ...settings },
    };
    const next: Conversation = {
      ...conversation,
      updatedAt: Date.now(),
      messages: [...conversation.messages, { role: "user", id: uid(), content: question }, assistant],
    };
    setConversations((items) => [next, ...items.filter((item) => item.id !== next.id)]);
    setActiveId(next.id);
    setDraft("");
    setBusy(true);

    const controller = new AbortController();
    abort.current = controller;
    const started = performance.now();
    let firstToken: number | undefined;
    try {
      await streamChat(
        question,
        settings,
        priorTurns,
        (event) => {
          if (event.event === "sources") {
            patchAssistant(next.id, assistant.id, () => ({
              citations: event.data.citations,
              trace: event.data.trace,
              standaloneQuestion: event.data.standalone_question,
            }));
          } else if (event.event === "status" && event.data.stage === "generation") {
            patchAssistant(next.id, assistant.id, () => ({ status: "generating" }));
          } else if (event.event === "delta") {
            firstToken ??= performance.now() - started;
            patchAssistant(next.id, assistant.id, (message) => ({
              status: "generating",
              content: message.content + event.data.text,
            }));
          } else if (event.event === "done") {
            patchAssistant(next.id, assistant.id, () => ({
              status: "done",
              totalMs: event.data.total_ms,
              firstTokenMs: firstToken,
              requestId: event.data.request_id,
            }));
          } else if (event.event === "error") {
            patchAssistant(next.id, assistant.id, () => ({ status: "error", error: event.data.detail }));
          }
        },
        controller.signal,
      );
      patchAssistant(next.id, assistant.id, (message) =>
        message.status === "retrieving" || message.status === "generating"
          ? { status: "error", error: "The connection closed before the answer finished." }
          : {},
      );
    } catch (caught) {
      const stopped = controller.signal.aborted;
      patchAssistant(next.id, assistant.id, () =>
        stopped
          ? { status: "stopped" }
          : { status: "error", error: caught instanceof Error ? caught.message : "Request failed" },
      );
    } finally {
      setBusy(false);
      abort.current = null;
    }
  }

  function retry() {
    if (!active || busy) return;
    const messages = active.messages;
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    if (!lastUser) return;
    const trimmed = { ...active, messages: messages.slice(0, messages.lastIndexOf(lastUser)) };
    void ask(lastUser.content, trimmed);
  }

  function remove(id: string) {
    setConversations((items) => items.filter((item) => item.id !== id));
    if (id === activeId) setActiveId(null);
  }

  return (
    <div className="shell">
      <Sidebar
        open={sidebar}
        onClose={() => setSidebar(false)}
        view={view}
        onView={(value) => {
          setView(value);
          setSidebar(false);
        }}
        conversations={conversations}
        activeId={activeId}
        onSelect={(id) => {
          setActiveId(id);
          setView("chat");
          setSidebar(false);
        }}
        onNew={newChat}
        onDelete={remove}
        index={index}
        health={health}
        online={online}
        theme={theme}
        onTheme={setTheme}
      />
      <main className="main">
        <header className="topbar">
          <button className="icon-button mobile-only" onClick={() => setSidebar(true)} aria-label="Open menu">
            <Menu size={18} />
          </button>
          <div className="mobile-only topbar-brand">
            <Brand />
          </div>
          <h2 className="topbar-title desktop-only">
            {view === "eval" ? "Evaluation" : (active?.title ?? "New chat")}
          </h2>
          <div className="topbar-actions">
            {view === "chat" && <SettingsMenu settings={settings} onChange={setSettings} />}
            <button className="icon-button mobile-only" onClick={newChat} aria-label="New chat">
              <SquarePen size={18} />
            </button>
          </div>
        </header>
        {view === "eval" ? (
          <div className="scroll">
            <EvalView />
          </div>
        ) : (
          <>
            <div className="scroll">
              <ChatView
                conversation={active}
                onPick={(text) => void ask(text, null)}
                onRetry={retry}
                indexEmpty={index != null && index.window.pages + index.heading.pages === 0}
              />
            </div>
            <Composer
              value={draft}
              onChange={setDraft}
              busy={busy}
              disabled={online === false}
              onStop={() => abort.current?.abort()}
              onSubmit={() => void ask(draft.trim(), active)}
            />
          </>
        )}
      </main>
    </div>
  );
}
