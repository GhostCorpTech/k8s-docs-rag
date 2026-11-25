# Case study: an assistant that answers from your documentation

**English** · [Русский](CASE_STUDY.ru.md)

## The problem

Teams lose time searching large technical documentation: engineers on call, support agents, new hires. A general-purpose chatbot answers fluently but cannot say *where* the answer comes from, mixes versions and invents flags and settings. For operational questions, an answer that cannot be checked is worse than no answer.

## What was built

An assistant over the official Kubernetes documentation (1,730 pages, ~32,000 indexed passages) that:

- answers in the user's language — tested with Russian questions over English documentation;
- grounds every answer in retrieved passages and links each claim to the exact section on kubernetes.io;
- shows the sources *before* the answer starts streaming, so the user sees what it is based on;
- keeps itself up to date: when the documentation changes, only changed pages are re-processed.

Kubernetes stands in for any large documentation set: an internal wiki, a product knowledge base, regulations, API references.

## Results

Measured on 50 real questions with known correct pages ([full report](EVALUATION.md)):

| What matters to the business | Result |
|---|---|
| The right page is among the top 5 sources | **48 of 50** questions |
| The right page is among the top 10 sources | **50 of 50** questions |
| Answer claims supported by the cited sources | **97.5 %** (automated check) |
| First words of the answer | **≈ 3 s** median with streaming (27 measured answers; 2–6 s for the middle half, longer when the provider is overloaded) |
| Questions the docs cannot answer | **0 of 20 fabricated** — it says so, or corrects a wrong premise |
| LLM cost per answered question | **≈ $0.0003** — about **$0.33 per 1,000 questions** |
| Cost of indexing the documentation | **$0** — embeddings run locally |

Prices are those of the inexpensive model used in the benchmark (`glm-5.3-flash`); a premium model multiplies the per-question cost, typically to cents per question. The local search itself costs nothing per query.

## Decisions that came from measurement, not habit

- **An "obvious" upgrade was switched off.** An AI reranker — a common recommendation — added 17 seconds per question and nearly tripled the LLM cost of an answer without improving results on this data. It stays available as an option.
- **The biggest gain came from fixing data preparation.** An audit of the first version found that up to a third of the text passages were silently truncated before indexing, and code examples were dropped. Fixing that more than doubled the quality of the top result (MRR 0.35 → 0.79) — more than any added component.
- **It was tested on what it should *not* answer.** Invented flags, removed APIs and other products' questions were answered with "not in the documentation" or a correction — never with an invented answer.
- **Failures were rehearsed, not hoped away.** When the AI provider had an hour-long partial outage, the assistant kept answering from search instead of failing; when a backup turned out to be corrupted, it was detected by an independent check and rebuilt without re-processing the documents.
- **Every claim is reproducible.** All 200 evaluated answers, sources, scores and API costs are stored; the published numbers are regenerated from them by one command.

## Privacy and control

- Documents are embedded on your own machine or server; the full document set is never sent to an AI provider.
- Only the handful of passages relevant to a question are sent to the language model, together with the question.
- Works with any OpenAI-compatible provider, including self-hosted models, so the data can stay entirely inside your perimeter.
- Logs record cost, latency and errors without storing question or answer text.

## Adapting it to your documentation

What is reused as is: search, ranking, answer generation with citations, streaming chat UI, evaluation harness, cost tracking, incremental updates.

What is specific to each project:

1. **A connector to your sources** — Markdown/Git (ready), or Confluence, Notion, Google Drive, PDFs, a website.
2. **A test set of 30–100 real questions** with the pages that answer them — written together with your team; this is what makes quality measurable instead of anecdotal.
3. **The model and hosting choice** — by budget and privacy requirements.
4. **Access control**, if different users may see different documents.

## Technology

Python · FastAPI · Qdrant · SQLite FTS5 · multilingual-e5 (local ONNX) · any OpenAI-compatible LLM · React + TypeScript · Docker Compose · GitHub Actions.
