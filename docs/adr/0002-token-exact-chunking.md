# 0002 — Chunk with the embedding model's own tokenizer

**Status:** accepted · **Date:** 2026-11-19

## Context
`multilingual-e5-base` embeds at most 512 tokens and silently truncates the rest. v1 split pages by character count. An audit with the real tokenizer found 3,935 / 18,121 heading chunks and 3,940 / 12,019 window chunks over the limit (max 1,417 tokens): up to a third of the text was never embedded.

## Options
1. Characters with a safety margin — cheap, but the chars-per-token ratio varies 3× between prose, YAML and Russian text, so any margin either wastes context or still overflows.
2. An approximate tokenizer (tiktoken) — wrong vocabulary for E5.
3. **The exact E5 tokenizer**, counting the `passage:` prefix, the title/heading prefix and special tokens.

## Decision
Option 3. Windows are cut at 512 tokens with 64 tokens of overlap and snapped back to a paragraph, line or space boundary in the last 35 % of the window. The tokenizer is loaded from the local model cache; the indexer refuses to fall back to character estimates.

## Consequences
- 0 of 32,088 chunks exceed the limit (max 509); `app.cli validate-chunking` checks every page offline.
- Chunking needs the model files present — acceptable, the same files are needed for embedding.
- The chunker version is part of every page's index hash, so changing chunking forces re-indexing instead of mixing old and new vectors.
