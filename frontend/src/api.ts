import type { ChatResponse, EvalResponse, Strategy } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8088";

async function check(response: Response) {
  if (response.ok) return response.json();
  const body = await response.json().catch(() => ({}));
  throw new Error(body.detail ?? `HTTP ${response.status}`);
}

export async function ask(
  question: string,
  strategy: Strategy,
  rerank: boolean,
): Promise<ChatResponse> {
  return check(
    await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, strategy, rerank, limit: 8 }),
    }),
  );
}

export async function getEval(): Promise<EvalResponse> {
  return check(await fetch(`${API_URL}/api/eval/latest`));
}

export async function getStatus(): Promise<
  Record<Strategy, { pages: number; chunks: number; last_indexed: string | null }>
> {
  return check(await fetch(`${API_URL}/api/index/status`));
}
