import { useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  BookOpen,
  CheckCircle2,
  ExternalLink,
  LoaderCircle,
  Send,
  Settings2,
} from "lucide-react";
import { ask, getEval, getStatus } from "./api";
import type { ChatResponse, EvalResponse, Strategy } from "./types";

const examples = [
  "Чем Deployment отличается от StatefulSet?",
  "Как настроить liveness и readiness probes?",
  "Как работает Horizontal Pod Autoscaler?",
  "Что происходит при eviction Pod?",
];

type HistoryItem = { question: string; response: ChatResponse };

function MetricTable({ evaluation }: { evaluation: EvalResponse }) {
  const { metrics, run_id: runId } = evaluation;
  if (!runId) {
    return (
      <div className="empty-state">
        <BarChart3 size={34} />
        <h3>Eval ещё не запускался</h3>
        <p>Выполните <code>python -m app.cli evaluate</code>, и таблица появится здесь.</p>
      </div>
    );
  }
  return (
    <section className="metrics-card">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Последний прогон</span>
          <h2>{runId}</h2>
        </div>
        <span className="badge"><CheckCircle2 size={14} /> {metrics[0]?.questions ?? 0} вопросов</span>
      </div>
      {evaluation.configuration && <p>Роутер: {evaluation.configuration.api_base_url} · Модель: {evaluation.configuration.chat_model}</p>}
      {evaluation.faithfulness_verified === false && (
        <div className="error">Исторический прогон: Recall пересчитан из сохранённой выдачи. Faithfulness не проверен — старый judge видел лишние фрагменты, а ошибки JSON могли стать нулевыми оценками. Эти результаты нельзя напрямую сравнивать с новыми токенными индексами.</div>
      )}
      <p>Recall@k — средняя доля найденных релевантных страниц. MRR@10 — обратный ранг первого релевантного чанка. Совпадение проверяется по странице, без якоря.</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Chunking</th><th>Reranker</th><th>Recall@5</th>
              <th>Recall@10</th><th>MRR@10</th><th>Faithfulness</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric) => (
              <tr key={`${metric.strategy}-${metric.reranker}`}>
                <td><strong>{metric.strategy}</strong></td>
                <td>{metric.reranker ? "on" : "off"}</td>
                <td>{metric.recall_at_5.toFixed(3)}</td>
                <td>{metric.recall_at_10.toFixed(3)}</td>
                <td>{metric.mrr_at_10.toFixed(3)}</td>
                <td>{metric.faithfulness == null ? "—" : metric.faithfulness.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function App() {
  const [tab, setTab] = useState<"chat" | "eval">("chat");
  const [question, setQuestion] = useState("");
  const [strategy, setStrategy] = useState<Strategy>("heading");
  const [rerank, setRerank] = useState(true);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [evaluation, setEvaluation] = useState<EvalResponse>({ run_id: null, metrics: [] });
  const [status, setStatus] = useState("Индекс не проверен");

  useEffect(() => {
    getStatus()
      .then((value) => setStatus(`${value.heading.pages} страниц · ${value.heading.chunks} чанков`))
      .catch(() => setStatus("Backend недоступен"));
    getEval().then((value) => {
      setEvaluation(value);
    }).catch(() => undefined);
  }, []);

  const canSend = useMemo(() => question.trim().length > 1 && !loading, [question, loading]);

  async function submit() {
    if (!canSend) return;
    const current = question.trim();
    setLoading(true);
    setError("");
    try {
      const response = await ask(current, strategy, rerank);
      setHistory((items) => [...items, { question: current, response }]);
      setQuestion("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Неизвестная ошибка");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header>
        <div className="brand">
          <div className="brand-mark">K8s</div>
          <div><strong>Docs Assistant</strong><span>Official documentation RAG</span></div>
        </div>
        <nav>
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
            <BookOpen size={17} /> Чат
          </button>
          <button className={tab === "eval" ? "active" : ""} onClick={() => setTab("eval")}>
            <BarChart3 size={17} /> Метрики
          </button>
        </nav>
        <div className="status-dot"><i />{status}</div>
      </header>

      <main>
         {tab === "eval" ? <MetricTable evaluation={evaluation} /> : (
          <>
            {history.length === 0 && (
              <section className="hero">
                <span className="eyebrow">Kubernetes knowledge, grounded</span>
                <h1>Спросите официальную<br /><em>документацию Kubernetes</em></h1>
                <p>Гибридный поиск находит релевантные разделы, reranker уточняет выдачу, а ответ всегда содержит источники.</p>
                <div className="examples">
                  {examples.map((example) => <button key={example} onClick={() => setQuestion(example)}>{example}</button>)}
                </div>
              </section>
            )}

            <section className="conversation">
              {history.map((item, itemIndex) => (
                <article className="exchange" key={`${item.question}-${itemIndex}`}>
                  <div className="question-bubble">{item.question}</div>
                  <div className="answer-card">
                    <div className="answer-label"><BookOpen size={17} /> Ответ по документации</div>
                    <div className="answer-text">{item.response.answer}</div>
                    <div className="sources">
                      <h4>Источники</h4>
                      <div className="source-grid">
                        {item.response.citations.map((source) => (
                          <a href={source.url} target="_blank" rel="noreferrer" key={source.index}>
                            <span className="source-number">{source.index}</span>
                            <span><strong>{source.title}</strong><small>{source.heading}</small></span>
                            <ExternalLink size={15} />
                          </a>
                        ))}
                      </div>
                    </div>
                  </div>
                </article>
              ))}
            </section>

            <div className="composer-wrap">
              {error && <div className="error">{error}</div>}
              <div className="composer">
                <textarea
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); }
                  }}
                   placeholder="Например: как ограничить ресурсы контейнера?"
                   maxLength={4000}
                  rows={2}
                />
                <button className="send" disabled={!canSend} onClick={submit} aria-label="Отправить">
                  {loading ? <LoaderCircle className="spin" size={20} /> : <Send size={20} />}
                </button>
                <div className="controls">
                  <Settings2 size={15} />
                  <select value={strategy} onChange={(event) => setStrategy(event.target.value as Strategy)}>
                    <option value="heading">Chunking: heading</option>
                    <option value="window">Chunking: window</option>
                  </select>
                  <label><input type="checkbox" checked={rerank} onChange={(event) => setRerank(event.target.checked)} /> Reranker</label>
                </div>
              </div>
              <p className="disclaimer">Ответы генерируются моделью — проверяйте важные команды по приведённым ссылкам.</p>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
