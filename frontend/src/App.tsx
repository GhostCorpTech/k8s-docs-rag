import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { KeyRound, Menu, SquarePen } from "lucide-react";
import { ApiError, getDemo, getHealth, getIndexStatus, getSession, streamChat, type ChatTurn } from "./api";
import { ChatView } from "./components/ChatView";
import { Composer, LockedComposer } from "./components/Composer";
import { EvalView } from "./components/EvalView";
import { Landing } from "./components/Landing";
import { SettingsMenu } from "./components/SettingsMenu";
import { Brand, Sidebar, type Theme, type View } from "./components/Sidebar";
import { clearKey, keyFromLink, loadKey, saveKey } from "./lib/access";
import { STRINGS, loadLang, saveLang, type Lang } from "./lib/i18n";
import { loadConversations, loadSettings, saveConversations, saveSettings, titleFrom, uid } from "./lib/store";
import type {
  AssistantMessage,
  Conversation,
  DemoInfo,
  DemoSession,
  Health,
  IndexStatus,
  RetrievalSettings,
  Strategy,
} from "./types";

/** open: no demo gate · locked: demo without a valid key (landing + read-only) · granted: valid key */
type Access = "loading" | "open" | "locked" | "granted";

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

  // Public demo gate
  const [linkKey] = useState(keyFromLink);
  const [demo, setDemo] = useState<DemoInfo | null>(null);
  const [access, setAccess] = useState<Access>("loading");
  const [session, setSession] = useState<DemoSession | null>(null);
  const [lockReason, setLockReason] = useState<string | null>(null);
  const [explore, setExplore] = useState(() => location.hash.startsWith("#/eval"));
  const [sample, setSample] = useState<Conversation | null>(null);
  const [lang, setLang] = useState<Lang>(loadLang);
  const t = STRINGS[lang];
  const readOnly = access === "locked";

  const active = useMemo(
    () => (readOnly ? sample : (conversations.find((item) => item.id === activeId) ?? null)),
    [readOnly, sample, conversations, activeId],
  );

  /** Validate a key; returns an error code for the landing form, or null when access is granted. */
  const unlock = useCallback(async (key: string): Promise<string | null> => {
    try {
      const value = await getSession(key);
      saveKey(key);
      setSession(value);
      setAccess("granted");
      setExplore(false);
      setLockReason(null);
      return null;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clearKey();
        setAccess("locked");
        return error.code ?? "invalid_key";
      }
      setAccess((current) => (current === "granted" ? current : "locked"));
      return error instanceof ApiError && error.code ? error.code : "network";
    }
  }, []);

  const lock = useCallback((reason: string | null) => {
    clearKey();
    setSession(null);
    setLockReason(reason);
    setExplore(false);
    setAccess("locked");
  }, []);

  useEffect(() => {
    getDemo()
      .then(async (info) => {
        setDemo(info);
        if (!info.enabled) return setAccess("open");
        if (linkKey) saveKey(linkKey);
        const stored = loadKey();
        if (!stored) return setAccess("locked");
        setLockReason(await unlock(stored));
      })
      .catch(() => setAccess("open")); // older backend or offline: the normal UI reports it
  }, [linkKey, unlock]);

  useEffect(() => {
    if (!readOnly || sample) return;
    fetch("/sample-conversation.json")
      .then((response) => (response.ok ? (response.json() as Promise<Conversation>) : null))
      .then((value) => value && setSample(value))
      .catch(() => undefined);
  }, [readOnly, sample]);

  useEffect(() => {
    document.documentElement.lang = lang;
    saveLang(lang);
  }, [lang]);

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
    if (readOnly) return;
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
      if (caught instanceof ApiError && caught.status === 401) lock(caught.code);
    } finally {
      setBusy(false);
      abort.current = null;
      if (access === "granted")
        getSession()
          .then(setSession)
          .catch((error) => error instanceof ApiError && error.status === 401 && lock(error.code));
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

  if (access === "loading") return <div className="boot" aria-busy />;

  if (access === "locked" && !explore && demo)
    return (
      <Landing
        demo={demo}
        t={t}
        lang={lang}
        onLang={setLang}
        reason={lockReason}
        onUnlock={unlock}
        onSample={() => {
          setExplore(true);
          setView("chat");
        }}
        onEval={() => {
          setExplore(true);
          setView("eval");
        }}
      />
    );

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
        conversations={readOnly ? (sample ? [sample] : []) : conversations}
        activeId={readOnly ? (sample?.id ?? null) : activeId}
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
        locked={
          readOnly ? { label: t.enterKey, sectionLabel: t.sampleLabel, onUnlock: () => setExplore(false) } : undefined
        }
        session={access === "granted" ? session : null}
        onForgetKey={access === "granted" ? () => lock(null) : undefined}
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
            {view === "chat" && !readOnly && <SettingsMenu settings={settings} onChange={setSettings} />}
            {readOnly ? (
              <button className="icon-button mobile-only" onClick={() => setExplore(false)} aria-label={t.enterKey}>
                <KeyRound size={18} />
              </button>
            ) : (
              <button className="icon-button mobile-only" onClick={newChat} aria-label="New chat">
                <SquarePen size={18} />
              </button>
            )}
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
                onRetry={readOnly ? undefined : retry}
                startAtTop={readOnly}
                indexEmpty={index != null && index.window.pages + index.heading.pages === 0}
              />
            </div>
            {readOnly ? (
              <LockedComposer text={t.lockedBar} action={t.enterKey} onUnlock={() => setExplore(false)} />
            ) : (
              <Composer
                value={draft}
                onChange={setDraft}
                busy={busy}
                disabled={online === false}
                onStop={() => abort.current?.abort()}
                onSubmit={() => void ask(draft.trim(), active)}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
