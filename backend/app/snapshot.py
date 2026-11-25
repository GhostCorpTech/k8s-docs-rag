"""Export and restore a complete index (Qdrant collections + SQLite) as one archive.

The archive is what a GitHub Release ships so that a fresh checkout can answer questions
in minutes instead of re-embedding ~32k chunks. Every file is listed in a manifest with
its SHA-256; restore refuses on any mismatch and loads the collections under the
*configured* collection prefix.
"""

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .config import Settings
from .database import Database
from .docs import current_commit
from .indexer import CHUNKER_VERSION
from .vector_store import VectorStore

STRATEGIES = ("heading", "window")
FORMAT_VERSION = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _http(settings: Settings) -> httpx.Client:
    headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
    return httpx.Client(base_url=settings.qdrant_url.rstrip("/"), headers=headers, timeout=600)


def export_index(settings: Settings, archive: Path) -> dict:
    store = VectorStore(settings)
    with tempfile.TemporaryDirectory() as temp, _http(settings) as http:
        root = Path(temp)
        files = []
        collections = {}
        for strategy in STRATEGIES:
            name = settings.collection(strategy)
            snapshot = http.post(f"/collections/{name}/snapshots", params={"wait": "true"})
            snapshot.raise_for_status()
            snapshot_name = snapshot.json()["result"]["name"]
            target = root / "snapshots" / f"{strategy}.snapshot"
            target.parent.mkdir(parents=True, exist_ok=True)
            with http.stream("GET", f"/collections/{name}/snapshots/{snapshot_name}") as response:
                response.raise_for_status()
                with target.open("wb") as file:
                    for chunk in response.iter_bytes(1 << 20):
                        file.write(chunk)
            http.delete(f"/collections/{name}/snapshots/{snapshot_name}")
            collections[strategy] = {
                "points": store.client.count(name, exact=True).count,
                "file": f"snapshots/{strategy}.snapshot",
            }
        sqlite_target = root / "sqlite" / "rag.db"
        sqlite_target.parent.mkdir(parents=True)
        with (
            closing(sqlite3.connect(settings.sqlite_path)) as source,
            closing(sqlite3.connect(sqlite_target)) as dest,
        ):
            source.backup(dest)
        index_status = Database(sqlite_target).status()
        connection = sqlite3.connect(sqlite_target)
        connection.execute("PRAGMA journal_mode=DELETE")  # one self-contained file, no -wal
        connection.close()
        for suffix in ("-wal", "-shm"):
            Path(f"{sqlite_target}{suffix}").unlink(missing_ok=True)
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        manifest = {
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "documentation_commit": current_commit(settings.docs_repo_path),
            "chunker_version": CHUNKER_VERSION,
            "embedding": settings.embedding_identity(),
            "index_status": index_status,
            "collections": collections,
            "files": files,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                bundle.write(path, path.relative_to(root).as_posix())
    return manifest


def restore_index(settings: Settings, archive: Path, *, force: bool = False) -> dict:
    store = VectorStore(settings)
    existing = [s for s in STRATEGIES if store.exists(s)]
    has_sqlite_index = (
        settings.sqlite_path.exists()
        and Database(settings.sqlite_path).status()["window"]["pages"] > 0
    )
    if (existing or has_sqlite_index) and not force:
        raise RuntimeError(
            f"An index already exists (collections: {existing or 'none'}, {settings.sqlite_path}). "
            "Use --force to replace it."
        )
    with tempfile.TemporaryDirectory() as temp, _http(settings) as http:
        root = Path(temp)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(root)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest["files"]:
            if _sha256(root / item["path"]) != item["sha256"]:
                raise RuntimeError(f"Checksum mismatch for {item['path']}; the archive is damaged")
        for strategy, entry in manifest["collections"].items():
            name = settings.collection(strategy)
            with (root / entry["file"]).open("rb") as file:
                response = http.post(
                    f"/collections/{name}/snapshots/upload",
                    params={"priority": "snapshot", "wait": "true"},
                    files={"snapshot": (f"{strategy}.snapshot", file)},
                )
            response.raise_for_status()
            restored = store.client.count(name, exact=True).count
            if restored != entry["points"]:
                raise RuntimeError(
                    f"{name}: restored {restored} points, expected {entry['points']}"
                )
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{settings.sqlite_path}{suffix}").unlink(missing_ok=True)
        shutil.copyfile(root / "sqlite" / "rag.db", settings.sqlite_path)
    return {
        "documentation_commit": manifest["documentation_commit"],
        "chunker_version": manifest["chunker_version"],
        "collections": {s: settings.collection(s) for s in manifest["collections"]},
        "index_status": Database(settings.sqlite_path).status(),
    }
