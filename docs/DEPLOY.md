# Deploying the public demo

The demo runs as a separate Compose project behind a reverse proxy you already operate (nginx, a WAF, Caddy…), which terminates TLS. Anyone can open the landing page, the evaluation dashboard and a recorded answer. The live chat calls a paid LLM API, so it needs a personal access key that you issue, limit and revoke from the command line. Why keys rather than an open demo or a shared password: [ADR 0007](adr/0007-demo-access-keys.md).

## What is public and what needs a key

| Visitor sees | Key | Cost |
|---|---|---|
| Landing page: what the demo is, how to get a key, contacts | — | none |
| Recorded answer with sources and retrieval details | — | none (static file) |
| Evaluation dashboard (`/#/eval`), `/api/eval/*`, `/api/index/status`, `/health` | — | none |
| Live chat: `/api/chat/stream`, `/api/chat`, `/api/search` | required | LLM calls |

- **Keys**: `k8s_` + 144 random bits, shown once at creation. The server stores only a SHA-256 hash and a short hint (`k8s_AbCd…wxyz`).
- **Quotas**: questions per UTC day, per key (default 20) and for the whole server (default 200). A question whose answer fails on the provider side is refunded.
- **Abuse**: more than 10 invalid keys from one client address within 10 minutes → HTTP 429 for that address.
- **Revocation and expiry** take effect on the next request; the browser then returns to the landing page with an explanation.
- **Logs**: every LLM call in `data/diagnostics/live-api.jsonl` carries `access_key_id` with usage and latency. As before, no question or answer text is logged.

## Requirements

- Linux server with Docker Engine and Compose v2. Minimum 2 vCPU and 4 GB RAM; about 6 GB of disk (index ~0.6 GB, E5 model ~1.1 GB, images).
- A reverse proxy with HTTPS. Keys are bearer tokens, so never serve the demo over plain HTTP.
- An API key for an OpenAI-compatible endpoint.

## 1. Code and index

```bash
git clone https://github.com/<owner>/<repo>.git k8s-docs-rag && cd k8s-docs-rag
cp .env.example .env
mkdir -p data
curl -fL -o data/k8s-docs-index-v4.zip https://github.com/<owner>/<repo>/releases/latest/download/k8s-docs-index-v4.zip
sha256sum data/k8s-docs-index-v4.zip   # 45af04c6490bf113030d7aca7eaaacb31bf8414484bbef5d420d7a7ed70482ca
```

## 2. Configure `.env`

| Variable | Value |
|---|---|
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `CHAT_MODEL`, `RERANK_MODEL`, `JUDGE_MODEL` | your provider |
| `LOCAL_EMBEDDING_THREADS` | number of vCPUs, e.g. `2` |
| `DEMO_PORT` | local port your proxy forwards to (default `8090`) |
| `DEMO_PUBLIC_URL` | e.g. `https://demo.example.com`; `create-key` then prints a ready link |
| `DEMO_REPOSITORY_URL` | GitHub URL shown on the landing page |
| `DEMO_CONTACT_EMAIL`, `DEMO_CONTACT_TELEGRAM` | where visitors ask for a key |
| `DEMO_KEY_DAYS`, `DEMO_KEY_DAILY_LIMIT`, `DEMO_GLOBAL_DAILY_LIMIT` | key defaults and the server-wide daily cap |

## 3. Start and restore the index

```bash
alias demo='docker compose -f docker-compose.demo.yml'
demo up -d --build
demo exec backend python -m app.cli restore-index /app/data/k8s-docs-index-v4.zip
demo exec backend python -m app.cli verify-index --sample 20
curl -s http://127.0.0.1:8090/api/demo
```

`verify-index` also downloads the E5 model on first use (~1.1 GB) and checks vectors against the stored text. Only the web container is published, and only on `127.0.0.1`. Qdrant and the API are reachable only inside the Compose network.

## 4. Reverse proxy

nginx example (inside your `server { … }` with TLS):

```nginx
location / {
    proxy_pass http://127.0.0.1:8090;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Connection "";
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;        # answers stream as Server-Sent Events
    proxy_read_timeout 300s;
}
```

Check these points in your proxy and WAF:

- **Do not buffer** `text/event-stream` responses. Otherwise the answer appears all at once at the end.
- **Allow** JSON `POST` bodies up to ~300 KB with Cyrillic text to `/api/chat/stream` (a follow-up carries the conversation history), and do not strip the `Authorization` header.
- **Client address.** The web container trusts `X-Forwarded-For` only from loopback and private addresses, and resolves the real client from it (used for the invalid-key throttle). If your proxy reaches the container from a public address, add that address with `set_real_ip_from` in `frontend/nginx.conf`.
- **Proxy in a container.** If the proxy itself runs in Docker, set `DEMO_BIND` (for example to the Docker bridge address) or attach the proxy to the demo's network.

## 5. Issue and revoke keys

```bash
demo exec backend python -m app.cli create-key --label "hh: Acme, backend lead"
demo exec backend python -m app.cli create-key --label "client: Foo" --days 14 --daily-limit 50
demo exec backend python -m app.cli list-keys
demo exec backend python -m app.cli revoke-key 3
demo exec backend python -m app.cli extend-key 3 --days 30
```

`create-key` prints the key and, with `DEMO_PUBLIC_URL` set, a link such as `https://demo.example.com/#/?key=k8s_…`. The key sits in the URL fragment, which browsers never send to the server, so it does not reach proxy logs. The page stores it in the visitor's browser and removes it from the address bar. `list-keys` shows each key's state, today's usage, total questions and last use. The label is never shown to the visitor.

## 6. Cost bounds

The server-wide cap bounds the worst case: 200 questions a day, each at most 2–4 LLM calls (translation, answer; with history also the follow-up rewrite, with the reranker one more). The benchmark model cost about $0.0005–0.001 per answer, so the cap is roughly $0.1–0.3 a day. Lower `DEMO_GLOBAL_DAILY_LIMIT` for a tighter bound.

## 7. Updates and backups

```bash
git pull && demo up -d --build   # the index survives: Qdrant volume + data/rag.db
```

- **Keys** live in `data/access.db`. Back it up together with `.env`.
- **Newer docs.** `demo exec backend python -m app.cli index --sync --strategy both` re-embeds only the pages that changed.
