import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ExternalLink } from "lucide-react";
import { getEval, getEvalQuestions } from "../api";
import type { EvalMetric, EvalQuestion, EvalSummary } from "../types";
import { configLabel, fmt } from "../lib/format";

const CONFIGS = ["heading_rerank_off", "heading_rerank_on", "window_rerank_off", "window_rerank_on"];
const key = (metric: EvalMetric) => `${metric.strategy}_rerank_${metric.reranker ? "on" : "off"}`;
const BUCKETS = [
  { id: "r1", label: "Rank 1", test: (rank: number | null) => rank === 1 },
  { id: "r3", label: "Rank 2–3", test: (rank: number | null) => rank != null && rank >= 2 && rank <= 3 },
  { id: "r5", label: "Rank 4–5", test: (rank: number | null) => rank != null && rank >= 4 && rank <= 5 },
  { id: "r10", label: "Rank 6–10", test: (rank: number | null) => rank != null && rank >= 6 && rank <= 10 },
  { id: "miss", label: "Not in top 10", test: (rank: number | null) => rank == null || rank > 10 },
];
const bucketOf = (rank: number | null) => BUCKETS.find((bucket) => bucket.test(rank))!.id;

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  );
}

function RankDistribution({ questions }: { questions: EvalQuestion[] }) {
  return (
    <div className="rank-dist">
      <div className="legend">
        {BUCKETS.map((bucket) => (
          <span key={bucket.id}>
            <i className={`swatch ${bucket.id}`} />
            {bucket.label}
          </span>
        ))}
      </div>
      {CONFIGS.map((config) => {
        const [strategy, , state] = config.split("_");
        const counts = BUCKETS.map((bucket) => ({
          ...bucket,
          count: questions.filter((question) => bucket.test(question.results[config]?.rank ?? null)).length,
        }));
        return (
          <div className="dist-row" key={config}>
            <span className="dist-label">{configLabel(strategy, state === "on")}</span>
            <div className="dist-bar">
              {counts.map(
                (bucket) =>
                  bucket.count > 0 && (
                    <span
                      key={bucket.id}
                      className={`seg ${bucket.id}`}
                      style={{ flexGrow: bucket.count }}
                      title={`${bucket.label}: ${bucket.count} of ${questions.length} questions`}
                    >
                      {bucket.count >= 3 ? bucket.count : ""}
                    </span>
                  ),
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function QuestionMatrix({ questions }: { questions: EvalQuestion[] }) {
  const [onlyDisagreements, setOnlyDisagreements] = useState(false);
  const rows = useMemo(
    () =>
      onlyDisagreements
        ? questions.filter(
            (question) => new Set(CONFIGS.map((config) => question.results[config]?.rank ?? null)).size > 1,
          )
        : questions,
    [questions, onlyDisagreements],
  );
  return (
    <>
      <div className="card-toolbar">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={onlyDisagreements}
            onChange={(event) => setOnlyDisagreements(event.target.checked)}
          />
          Only questions where configurations disagree (
          {questions.filter((q) => new Set(CONFIGS.map((c) => q.results[c]?.rank ?? null)).size > 1).length})
        </label>
      </div>
      <div className="table-scroll matrix-scroll">
        <table className="data-table matrix">
          <thead>
            <tr>
              <th>ID</th>
              <th>Question (RU) · gold page</th>
              {CONFIGS.map((config) => {
                const [strategy, , state] = config.split("_");
                return (
                  <th key={config} className="num">
                    {strategy === "window" ? "Window" : "Heading"}
                    <br />
                    <span className="muted">{state === "on" ? "rerank" : "hybrid"}</span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((question) => (
              <tr key={question.id}>
                <td className="mono muted">{question.id}</td>
                <td>
                  <div className="q-cell">
                    <span>{question.question}</span>
                    <a href={question.relevant_urls[0]} target="_blank" rel="noreferrer" className="gold">
                      {question.relevant_urls[0].replace("https://kubernetes.io/docs/", "")}
                      <ExternalLink size={11} />
                    </a>
                  </div>
                </td>
                {CONFIGS.map((config) => {
                  const result = question.results[config];
                  const rank = result?.rank ?? null;
                  return (
                    <td key={config} className="num">
                      <span
                        className={`rank ${bucketOf(rank)}`}
                        title={`First relevant page at rank ${rank ?? "—"} · faithfulness ${fmt(result?.faithfulness, 2)}`}
                      >
                        {rank ?? "–"}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function EvalView() {
  const [summary, setSummary] = useState<EvalSummary | null>(null);
  const [questions, setQuestions] = useState<EvalQuestion[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    getEval()
      .then(setSummary)
      .catch((caught: Error) => setError(caught.message));
    getEvalQuestions()
      .then((value) => setQuestions(value.questions))
      .catch(() => undefined);
  }, []);

  if (error)
    return (
      <div className="page">
        <div className="notice error">{error}</div>
      </div>
    );
  if (!summary)
    return (
      <div className="page">
        <p className="muted">Loading evaluation…</p>
      </div>
    );
  if (!summary.run_id)
    return (
      <div className="page">
        <h1>Evaluation</h1>
        <p className="muted">
          No evaluation run found. Run <code>python -m app.cli evaluate --with-faithfulness</code>.
        </p>
      </div>
    );

  const metrics = summary.metrics;
  const best = [...metrics].sort((a, b) => b.mrr_at_10 - a.mrr_at_10)[0];
  const bestOf = (field: keyof EvalMetric) => Math.max(...metrics.map((metric) => Number(metric[field] ?? 0)));
  const cost = summary.billing?.known_token_cost_usd;
  const completed = summary.completed_at ? new Date(summary.completed_at) : null;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Evaluation</h1>
          <p className="muted">
            {summary.question_count ?? best.questions} Russian questions over English docs · 2 chunkers × reranker
            on/off · run <span className="mono">{summary.run_id}</span>
            {completed &&
              ` · ${completed.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}`}
          </p>
        </div>
      </header>

      {summary.faithfulness_verified === false && (
        <div className="notice warn">
          <AlertTriangle size={15} />
          <span>Legacy run: faithfulness is unverified and the index used truncated character windows.</span>
        </div>
      )}

      <p className="best-line">
        Best configuration by MRR@10: <strong>{configLabel(best.strategy, best.reranker)}</strong>
      </p>
      <section className="stats">
        <Stat label="Recall@10" value={fmt(best.recall_at_10)} note="gold page in the top 10" />
        <Stat label="Recall@5" value={fmt(best.recall_at_5)} note="gold page in the top 5" />
        <Stat label="MRR@10" value={fmt(best.mrr_at_10)} note="rank of first relevant chunk" />
        <Stat label="Faithfulness" value={fmt(best.faithfulness)} note="LLM judge, grounded claims" />
        <Stat
          label="Benchmark API cost"
          value={cost != null ? `$${cost.toFixed(3)}` : "—"}
          note={summary.billing ? `${summary.billing.request_attempts} calls incl. retries` : undefined}
        />
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Retrieval & answer quality</h2>
          <span className="muted small">Best value per column in bold</span>
        </div>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Configuration</th>
                <th className="num">Recall@5</th>
                <th className="num">Recall@10</th>
                <th className="num">Hit@5</th>
                <th className="num">MRR@10</th>
                <th className="num">Faithfulness</th>
              </tr>
            </thead>
            <tbody>
              {CONFIGS.map((config) => metrics.find((metric) => key(metric) === config))
                .filter((metric): metric is EvalMetric => Boolean(metric))
                .map((metric) => (
                  <tr key={key(metric)} className={metric === best ? "highlight" : ""}>
                    <td>
                      {configLabel(metric.strategy, metric.reranker)}
                      {metric === best && <span className="tag">best</span>}
                    </td>
                    {(["recall_at_5", "recall_at_10", "hit_rate_at_5", "mrr_at_10", "faithfulness"] as const).map(
                      (field) => (
                        <td
                          key={field}
                          className={`num mono ${Number(metric[field]) === bestOf(field) ? "strong" : ""}`}
                        >
                          {fmt(metric[field] as number | null)}
                        </td>
                      ),
                    )}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">
          Recall@k: share of gold pages found in the top k (page-level, anchors ignored). MRR@10: reciprocal rank of the
          first relevant chunk. Faithfulness: share of answer claims supported by the exact context given to the
          generator (LLM judge — groundedness, not completeness).
        </p>
      </section>

      {questions.length > 0 && (
        <>
          <section className="card">
            <div className="card-head">
              <h2>Where does the first relevant page land?</h2>
            </div>
            <RankDistribution questions={questions} />
          </section>
          <section className="card">
            <div className="card-head">
              <h2>Per-question results</h2>
              <span className="muted small">Cell = rank of the first relevant page (– = not in top 10)</span>
            </div>
            <QuestionMatrix questions={questions} />
          </section>
        </>
      )}

      {summary.configuration && summary.index && (
        <section className="card">
          <div className="card-head">
            <h2>Run configuration</h2>
          </div>
          <dl className="kv grid">
            <dt>Generator / reranker / judge</dt>
            <dd className="mono">{summary.configuration.chat_model}</dd>
            <dt>Embeddings</dt>
            <dd className="mono">{summary.configuration.embedding_model} (local ONNX)</dd>
            <dt>Candidates</dt>
            <dd>
              {summary.configuration.dense_limit} dense + {summary.configuration.lexical_limit} BM25 → RRF (k=
              {summary.configuration.rrf_k}) → top {summary.configuration.final_limit} in context
            </dd>
            <dt>Index</dt>
            <dd>
              {summary.index.counts.window.pages.toLocaleString("en-US")} pages ·{" "}
              {summary.index.counts.heading.chunks.toLocaleString("en-US")} heading /{" "}
              {summary.index.counts.window.chunks.toLocaleString("en-US")} window chunks · chunker{" "}
              {summary.index.chunker_version}
            </dd>
            <dt>Docs snapshot</dt>
            <dd className="mono">kubernetes/website@{summary.index.source_commit.slice(0, 12)}</dd>
          </dl>
        </section>
      )}
    </div>
  );
}
