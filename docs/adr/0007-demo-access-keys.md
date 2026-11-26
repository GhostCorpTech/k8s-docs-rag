# 0007 — Public demo: personal access keys with daily quotas

**Status:** accepted · **Date:** 2026-11-26

## Context
The project needs a live demo that a reviewer can open from the README. Every chat question costs real money: it makes 2–4 calls to a paid LLM API (translation, answer, sometimes a follow-up rewrite or the reranker). An open endpoint can be drained by a script or a crawler. One of the two providers used during development does not even report token usage, so spend cannot be metered in dollars. Visitors are recruiters and potential clients, so sign-up friction has to stay low. The owner also needs to know who is using the demo and to cut off a single person without affecting others.

## Options
1. **Open demo with an IP rate limit.** No friction, but addresses are shared and easy to rotate. Worst-case spend is unbounded, and usage cannot be attributed to anyone.
2. **One shared password at the proxy (HTTP basic auth).** Simple, but one leak opens the demo to everyone. Access cannot be revoked for one person, and there is no per-person quota.
3. **OAuth login (e.g. GitHub).** Real identities, but it asks a non-technical visitor for an account. It also needs an OAuth app and session handling, which is out of proportion for a demo.
4. **Personal bearer keys issued by hand.** Each key is hashed at rest, expires, can be revoked on its own, and has a per-key daily quota. A server-wide daily cap sits on top.

## Decision
Option 4, behind a `DEMO_MODE` switch that is off for local use:

- Keys are 144-bit random tokens. Only a SHA-256 hash is stored, together with a short hint and an internal label that is never shown to the visitor.
- Quotas count **questions per UTC day**, not dollars: per key (default 20) and for the whole server (default 200). Checking a quota and incrementing it happen in one `BEGIN IMMEDIATE` SQLite transaction, so parallel requests cannot overshoot.
- An answer that fails on the provider side is refunded. A provider incident does not burn a visitor's quota (see [EVALUATION.md §11](../EVALUATION.md#11-live-checks-after-release)).
- After 10 invalid keys from one client address in 10 minutes, that address gets HTTP 429. The address comes from `X-Forwarded-For` and is trusted only from loopback and private hops, so a client cannot spoof it.
- Everything that costs nothing stays public: the landing page, the evaluation dashboard and a recorded answer. Nothing about the project's quality is hidden behind the key.
- Keys are managed from the CLI on the server (`create-key`, `list-keys`, `revoke-key`, `extend-key`). There is deliberately no admin web UI, which would be another surface to attack.

## Consequences
- Worst-case spend is bounded by the server cap: about $0.1–0.3 a day with the benchmark model.
- Issuing a key is a manual step. At portfolio volume this is a feature: every key request is a conversation with an interested person.
- The key lives in the visitor's browser storage, so an XSS bug would expose it. Mitigations: no third-party scripts, Markdown rendered without raw HTML, and keys that are personal, quota-bound and revocable.
- The quota does not distinguish cheap and expensive questions: a reranked follow-up counts as one question. This is acceptable with the server cap in place.
- Running the demo means two Compose files: `docker-compose.yml` for local use (open, all ports published) and `docker-compose.demo.yml` for the server (gated, only the web port, on loopback).
