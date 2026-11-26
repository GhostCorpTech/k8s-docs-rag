export type Strategy = "heading" | "window";

export interface Citation {
  index: number;
  title: string;
  heading: string;
  url: string;
  excerpt: string;
  score?: number | null;
  dense_rank?: number | null;
  lexical_rank?: number | null;
  rerank_score?: number | null;
}

export interface Trace {
  strategy: Strategy;
  lexical_query: string | null;
  dense_candidates: number;
  lexical_candidates: number;
  fused_candidates: number;
  rerank_requested: boolean;
  rerank_applied: boolean;
  degraded: string[];
  timings_ms: Record<string, number>;
}

export interface RetrievalSettings {
  strategy: Strategy;
  rerank: boolean;
  limit: number;
}

export interface UserMessage {
  role: "user";
  id: string;
  content: string;
}

export type AssistantStatus = "retrieving" | "generating" | "done" | "error" | "stopped";

export interface AssistantMessage {
  role: "assistant";
  id: string;
  content: string;
  status: AssistantStatus;
  citations: Citation[];
  settings: RetrievalSettings;
  trace?: Trace;
  standaloneQuestion?: string | null;
  error?: string;
  totalMs?: number;
  firstTokenMs?: number;
  requestId?: string;
}

export type Message = UserMessage | AssistantMessage;

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
}

export type StreamEvent =
  | { event: "status"; data: { stage: "retrieval" | "generation" } }
  | {
      event: "sources";
      data: {
        citations: Citation[];
        standalone_question: string | null;
        reranked: boolean;
        trace: Trace;
      };
    }
  | { event: "delta"; data: { text: string } }
  | { event: "done"; data: { request_id: string; total_ms: number } }
  | { event: "error"; data: { detail: string } };

export interface IndexStatus {
  pages: number;
  chunks: number;
  last_indexed: string | null;
}

export interface Health {
  status: string;
  version: string;
  chat_api_configured: boolean;
  chat_model: string;
  rerank_model: string;
  embedding_model: string;
}

export interface EvalMetric {
  strategy: Strategy;
  reranker: boolean;
  questions: number;
  recall_at_5: number;
  recall_at_10: number;
  mrr_at_10: number;
  faithfulness: number | null;
  hit_rate_at_5?: number;
  hit_rate_at_10?: number;
}

export interface EvalSummary {
  run_id: string | null;
  metrics: EvalMetric[];
  created_at?: string;
  completed_at?: string;
  question_count?: number;
  warnings?: string[];
  faithfulness_verified?: boolean;
  configuration?: {
    chat_model: string;
    rerank_model: string;
    judge_model: string;
    embedding_model: string;
    dense_limit: number;
    lexical_limit: number;
    rrf_k: number;
    final_limit: number;
  };
  index?: {
    source_commit: string;
    chunker_version: string;
    counts: Record<Strategy, { pages: number; chunks: number }>;
  };
  billing?: {
    request_attempts: number;
    prompt_tokens: number;
    completion_tokens: number;
    known_token_cost_usd: number | null;
    unknown_cost_attempts: number;
  };
}

export interface QuestionResult {
  rank: number | null;
  faithfulness: number | null;
  top_url: string | null;
}

export interface EvalQuestion {
  id: string;
  question: string;
  relevant_urls: string[];
  results: Record<string, QuestionResult>;
}

export interface DemoInfo {
  enabled: boolean;
  key_days: number;
  key_daily_limit: number;
  contact: { email: string | null; telegram: string | null };
  repository_url: string | null;
}

export interface DemoSession {
  enabled: boolean;
  expires_at?: string;
  daily_limit?: number;
  used_today?: number;
  remaining_today?: number;
  resets_at?: string;
}
