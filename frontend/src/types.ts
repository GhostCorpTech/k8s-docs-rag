export type Strategy = "heading" | "window";

export interface Citation {
  index: number;
  title: string;
  heading: string;
  url: string;
  excerpt: string;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  strategy: Strategy;
  reranked: boolean;
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
  faithfulness_questions?: number;
}

export interface EvalResponse {
  run_id: string | null;
  metrics: EvalMetric[];
  warnings?: string[];
  faithfulness_verified?: boolean;
  configuration?: { api_base_url: string; chat_model: string; rerank_model: string; judge_model: string };
}
