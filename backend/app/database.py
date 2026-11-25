import re
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS indexed_pages (
    doc_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    source_path TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (doc_id, strategy)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    strategy UNINDEXED,
    doc_id UNINDEXED,
    url UNINDEXED,
    title,
    heading,
    content,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE INDEX IF NOT EXISTS idx_pages_strategy ON indexed_pages(strategy);

CREATE TABLE IF NOT EXISTS query_cache (
    query TEXT PRIMARY KEY,
    lexical_query TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def page_hashes(self, strategy: str) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT doc_id, content_hash FROM indexed_pages WHERE strategy = ?", (strategy,)
            )
            return {row["doc_id"]: row["content_hash"] for row in rows}

    def delete_document(self, doc_id: str, strategy: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM chunks_fts WHERE doc_id = ? AND strategy = ?", (doc_id, strategy)
            )
            connection.execute(
                "DELETE FROM indexed_pages WHERE doc_id = ? AND strategy = ?", (doc_id, strategy)
            )

    def replace_document(
        self,
        *,
        page: dict,
        strategy: str,
        content_hash: str,
        git_commit: str,
        chunks: list[dict],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM chunks_fts WHERE doc_id = ? AND strategy = ?",
                (page["doc_id"], strategy),
            )
            connection.executemany(
                """
                INSERT INTO chunks_fts
                    (chunk_id, strategy, doc_id, url, title, heading, content)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk["chunk_id"],
                        strategy,
                        page["doc_id"],
                        chunk["url"],
                        page["title"],
                        chunk["heading"],
                        chunk["content"],
                    )
                    for chunk in chunks
                ],
            )
            connection.execute(
                """
                INSERT INTO indexed_pages
                    (doc_id, strategy, source_path, url, title, content_hash, git_commit, chunk_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id, strategy) DO UPDATE SET
                    source_path=excluded.source_path,
                    url=excluded.url,
                    title=excluded.title,
                    content_hash=excluded.content_hash,
                    git_commit=excluded.git_commit,
                    chunk_count=excluded.chunk_count,
                    indexed_at=CURRENT_TIMESTAMP
                """,
                (
                    page["doc_id"],
                    strategy,
                    page["source_path"],
                    page["url"],
                    page["title"],
                    content_hash,
                    git_commit,
                    len(chunks),
                ),
            )

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[\w][\w.-]*", query.lower(), flags=re.UNICODE)
        # Quoting every token avoids treating user input as FTS syntax.
        return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens[:40])

    def lexical_search(self, query: str, strategy: str, limit: int) -> list[dict]:
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        sql = """
            SELECT chunk_id, doc_id, url, title, heading, content,
                   bm25(chunks_fts, 0.0, 0.0, 0.0, 0.0, 3.0, 2.0, 1.0) AS bm25_score
            FROM chunks_fts
            WHERE chunks_fts MATCH ? AND strategy = ?
            ORDER BY bm25_score
            LIMIT ?
        """
        try:
            with self.connect() as connection:
                return [dict(row) for row in connection.execute(sql, (fts_query, strategy, limit))]
        except sqlite3.OperationalError:
            return []

    def cached_lexical_query(self, query: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT lexical_query FROM query_cache WHERE query = ?", (query,)
            ).fetchone()
            return row["lexical_query"] if row else None

    def cache_lexical_query(self, query: str, lexical_query: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO query_cache(query, lexical_query) VALUES (?, ?)
                ON CONFLICT(query) DO UPDATE SET lexical_query=excluded.lexical_query,
                    created_at=CURRENT_TIMESTAMP
                """,
                (query, lexical_query),
            )

    def status(self) -> dict[str, dict[str, int | str | None]]:
        result: dict[str, dict[str, int | str | None]] = {}
        with self.connect() as connection:
            for strategy in ("heading", "window"):
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS pages, COALESCE(SUM(chunk_count), 0) AS chunks,
                           MAX(indexed_at) AS last_indexed
                    FROM indexed_pages WHERE strategy = ?
                    """,
                    (strategy,),
                ).fetchone()
                result[strategy] = dict(row)
        return result

    def chunk_ids(self, doc_id: str, strategy: str) -> Iterable[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT chunk_id FROM chunks_fts WHERE doc_id = ? AND strategy = ?",
                (doc_id, strategy),
            ).fetchall()
            return [row["chunk_id"] for row in rows]
