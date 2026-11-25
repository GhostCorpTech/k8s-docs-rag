import type { EvalQuestion, EvalSummary, Health, IndexStatus, RetrievalSettings, Strategy, StreamEvent } from "./types";

// Empty by default: the UI is served behind the same origin as the API (nginx / Vite proxy).
const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

async function json<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  const body = await response.json().catch(() => ({}));
  throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
}

export const getHealth = async () => json<Health>(await fetch(`${API_URL}/health`));
export const getIndexStatus = async () =>
  json<Record<Strategy, IndexStatus>>(await fetch(`${API_URL}/api/index/status`));
export const getEval = async () => json<EvalSummary>(await fetch(`${API_URL}/api/eval/latest`));
export const getEvalQuestions = async () =>
  json<{ run_id: string | null; questions: EvalQuestion[] }>(await fetch(`${API_URL}/api/eval/latest/questions`));

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

/** POST /api/chat/stream and dispatch Server-Sent Events as they arrive. */
export async function streamChat(
  question: string,
  settings: RetrievalSettings,
  history: ChatTurn[],
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question, ...settings, history }),
    signal,
  });
  if (!response.ok || !response.body) await json(response);

  const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
      let name = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent({ event: name, data: JSON.parse(data.join("\n")) } as StreamEvent);
    }
  }
}
