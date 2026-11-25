import { AlertTriangle } from "lucide-react";
import type { AssistantMessage } from "../types";
import { fmt, lastHeading, ms } from "../lib/format";

const STAGES: [string, string][] = [
  ["embed", "Query embedding (E5)"],
  ["dense", "Dense search (Qdrant)"],
  ["translate", "RU→EN query for BM25"],
  ["bm25", "BM25 (SQLite FTS5)"],
  ["rerank", "LLM rerank"],
];

const DEGRADED: Record<string, string> = {
  rerank_failed: "The reranker failed, so sources are shown in hybrid (RRF) order.",
  translation_failed: "Query translation failed; BM25 used the original wording.",
};

export function Inspector({ message }: { message: AssistantMessage }) {
  const { trace, citations } = message;
  if (!trace) return null;
  const retrievalMs = Object.values(trace.timings_ms).reduce((sum, value) => sum + value, 0);
  const generationMs = message.totalMs != null ? Math.max(message.totalMs - retrievalMs, 0) : undefined;
  return (
    <div className="inspector">
      {trace.degraded.map((item) => (
        <div className="notice warn" key={item}>
          <AlertTriangle size={15} />
          <span>{DEGRADED[item.split(":")[0]] ?? item}</span>
        </div>
      ))}
      <dl className="kv">
        {message.standaloneQuestion && (
          <>
            <dt>Rewritten follow-up</dt>
            <dd>{message.standaloneQuestion}</dd>
          </>
        )}
        <dt>BM25 query</dt>
        <dd className="mono">{trace.lexical_query ?? "—"}</dd>
        <dt>Pipeline</dt>
        <dd>
          {trace.dense_candidates} dense + {trace.lexical_candidates} BM25 →{" "}
          {trace.fused_candidates ? `${trace.fused_candidates} fused` : "fused"} (RRF) → {citations.length} in context ·{" "}
          {trace.strategy} chunks ·{" "}
          {trace.rerank_applied ? "LLM reranked" : trace.rerank_requested ? "rerank skipped" : "no rerank"}
        </dd>
      </dl>

      {(retrievalMs > 0 || message.totalMs != null) && (
        <div className="timings">
          {STAGES.filter(([key]) => trace.timings_ms[key] != null).map(([key, label]) => (
            <div key={key}>
              <span>{label}</span>
              <span className="mono">{ms(trace.timings_ms[key])}</span>
            </div>
          ))}
          {generationMs != null && (
            <div>
              <span>Generation{message.firstTokenMs != null ? ` (first token ${ms(message.firstTokenMs)})` : ""}</span>
              <span className="mono">{ms(generationMs)}</span>
            </div>
          )}
          {message.totalMs != null && (
            <div className="total">
              <span>Total</span>
              <span className="mono">{ms(message.totalMs)}</span>
            </div>
          )}
        </div>
      )}

      <div className="table-scroll">
        <table className="data-table compact">
          <thead>
            <tr>
              <th>#</th>
              <th>Section</th>
              <th className="num" title="Rank in dense (vector) results">
                Dense
              </th>
              <th className="num" title="Rank in BM25 results">
                BM25
              </th>
              <th className="num" title="Reciprocal Rank Fusion score">
                RRF
              </th>
              <th className="num" title="LLM reranker relevance">
                Rerank
              </th>
            </tr>
          </thead>
          <tbody>
            {citations.map((item) => (
              <tr key={item.index}>
                <td className="mono">{item.index}</td>
                <td>
                  <a href={item.url} target="_blank" rel="noreferrer">
                    {item.title}
                  </a>
                  <span className="muted"> · {lastHeading(item.heading)}</span>
                </td>
                <td className="num mono">{item.dense_rank ?? "—"}</td>
                <td className="num mono">{item.lexical_rank ?? "—"}</td>
                <td className="num mono">{fmt(item.score, 4)}</td>
                <td className="num mono">{fmt(item.rerank_score, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {message.requestId && (
        <p className="muted small">
          Diagnostic ID: <span className="mono">{message.requestId}</span>
        </p>
      )}
    </div>
  );
}
