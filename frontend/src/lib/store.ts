import type { Conversation, RetrievalSettings } from "../types";

const CONVERSATIONS = "k8s-rag.conversations.v1";
const SETTINGS = "k8s-rag.settings.v1";

// Best configuration on the 50-question benchmark: window chunks, no LLM reranker.
export const DEFAULT_SETTINGS: RetrievalSettings = { strategy: "window", rerank: false, limit: 8 };

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage can be unavailable (private mode); the app keeps working in memory */
  }
}

export const loadConversations = () => read<Conversation[]>(CONVERSATIONS, []);
export const saveConversations = (items: Conversation[]) => write(CONVERSATIONS, items.slice(0, 50));
export const loadSettings = () => ({ ...DEFAULT_SETTINGS, ...read(SETTINGS, {}) });
export const saveSettings = (settings: RetrievalSettings) => write(SETTINGS, settings);

export const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36).slice(2);

export function titleFrom(text: string): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > 60 ? `${clean.slice(0, 57)}…` : clean;
}
