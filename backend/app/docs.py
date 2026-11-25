import hashlib
import re
import subprocess
import tomllib
import uuid
from bisect import bisect_right
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import frontmatter

from .token_budget import TokenBudget

K8S_NAMESPACE = uuid.UUID("00f4d206-a264-44c4-9e0e-30e9534d77ba")
SHORTCODE_RE = re.compile(r"{{[<%].*?[>%]}}", re.DOTALL)
HEADING_SHORTCODE_RE = re.compile(r'{{[<%]\s*heading\s+["\']([^"\']+)["\']\s*[>%]}}', re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
EXPLICIT_ANCHOR_RE = re.compile(r"\s*\{#([^}]+)}\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _outside_code(text: str, transform) -> str:
    parts, prose = [], []
    fence: str | None = None

    def flush() -> None:
        if prose:
            parts.append(transform("".join(prose)))
            prose.clear()

    for line in text.splitlines(keepends=True):
        match = FENCE_RE.match(line)
        if match and fence is None:
            flush()
            fence = match.group(1)
            parts.append(line)
        elif fence is not None:
            parts.append(line)
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
        else:
            prose.append(line)
    flush()
    return "".join(parts)


@dataclass
class Page:
    doc_id: str
    source_path: str
    url: str
    title: str
    raw_content: str

    @property
    def content_hash(self) -> str:
        indexed_source = f"{self.title}\0{self.url}\0{self.raw_content}"
        return hashlib.sha256(indexed_source.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        return asdict(self)


def _git(args: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True, encoding="utf-8"
    )
    return completed.stdout.strip()


def sync_repository(repo_path: Path, git_url: str, branch: str = "main") -> str:
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    if not (repo_path / ".git").exists():
        _git(["clone", "--depth", "1", "--branch", branch, git_url, str(repo_path)])
    else:
        current_branch = _git(["branch", "--show-current"], cwd=repo_path)
        if current_branch != branch or _git(["status", "--porcelain"], cwd=repo_path):
            raise RuntimeError(
                "Documentation checkout has local changes or is on a different branch; refusing to switch/discard user work"
            )
        # Keep ancestry to allow ff-only updates after multiple upstream commits.
        _git(["fetch", "origin", branch], cwd=repo_path)
        _git(["merge", "--ff-only", f"origin/{branch}"], cwd=repo_path)
    return current_commit(repo_path)


def current_commit(repo_path: Path) -> str:
    if not (repo_path / ".git").exists():
        return "unknown"
    return _git(["rev-parse", "HEAD"], cwd=repo_path)


def _derive_url(relative: Path, metadata: dict, section: str = "docs") -> str:
    if metadata.get("url"):
        url = str(metadata["url"])
        return (
            url
            if url.startswith("https://kubernetes.io/")
            else "https://kubernetes.io/" + url.strip("/") + "/"
        )
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "_index":
        parts.pop()
    slug = metadata.get("slug")
    if slug and parts and relative.stem != "_index":
        parts[-1] = str(slug).strip("/")
    path = "/".join(parts)
    return f"https://kubernetes.io/{section}/" + (f"{path.strip('/')}/" if path else "")


def _expand_shortcodes(
    text: str, source: Path, repo_path: Path, translations: dict, depth: int = 0
) -> str:
    """Resolve local code samples/includes; never follow arbitrary external paths."""
    if depth > 8:
        raise ValueError(f"Recursive Hugo include in {source}")

    def expand(match: re.Match) -> str:
        shortcode = match.group(0)
        heading = HEADING_SHORTCODE_RE.fullmatch(shortcode)
        if heading:
            key = heading.group(1)
            return translations.get(f"{key}_heading", {}).get(
                "other", key.replace("-", " ").title()
            )
        name = re.match(r"{{[<%]\s*(\w+)", shortcode)
        if name is None or name.group(1) not in {"code", "codenew", "code_sample", "include"}:
            return shortcode
        kind = name.group(1)
        file_match = re.search(r"""file\s*=\s*["']([^"']+)["']""", shortcode)
        if kind == "include":
            file_match = re.search(r"""include\s+["']([^"']+)["']""", shortcode)
        if file_match is None:
            return shortcode
        filename = file_match.group(1).lstrip("/")
        category = "includes" if kind == "include" else "examples"
        candidates = [source.parent / filename, repo_path / "content" / "en" / category / filename]
        for candidate in candidates:
            candidate = candidate.resolve()
            if not candidate.is_relative_to(repo_path.resolve()):
                continue
            if candidate.is_file():
                content = candidate.read_text(encoding="utf-8")
                if kind == "include":
                    return _expand_shortcodes(
                        content, candidate, repo_path, translations, depth + 1
                    )
                language = re.search(r"""language\s*=\s*["']([^"']+)["']""", shortcode)
                language = language.group(1) if language else candidate.suffix.lstrip(".")
                return f"\n````{language}\n{content.rstrip()}\n````\n"
        raise FileNotFoundError(f"Hugo {kind} sample {filename!r} referenced by {source} not found")

    return _outside_code(text, lambda prose: SHORTCODE_RE.sub(expand, prose))


def load_pages(repo_path: Path) -> dict[str, Page]:
    content_root = repo_path / "content" / "en"
    docs_root = content_root / "docs"
    if not docs_root.exists():
        raise FileNotFoundError(
            f"Documentation not found at {docs_root}. Run indexing with --sync first."
        )
    pages: dict[str, Page] = {}
    translation_path = repo_path / "i18n" / "en" / "en.toml"
    translations = (
        tomllib.loads(translation_path.read_text(encoding="utf-8"))
        if translation_path.exists()
        else {}
    )
    for section in ("docs", "releases"):
        section_root = content_root / section
        if not section_root.exists():
            continue
        for path in sorted(section_root.rglob("*.md")):
            relative = path.relative_to(section_root)
            raw = path.read_text(encoding="utf-8")
            post = frontmatter.loads(raw)
            if post.metadata.get("draft") is True:
                continue
            title = str(post.metadata.get("title") or relative.stem.replace("-", " ").title())
            # Preserve historical IDs for /docs so adding /releases stays incremental.
            doc_id = (
                relative.as_posix() if section == "docs" else f"{section}/{relative.as_posix()}"
            )
            pages[doc_id] = Page(
                doc_id=doc_id,
                source_path=path.as_posix(),
                url=_derive_url(relative, post.metadata, section),
                title=title,
                raw_content=_expand_shortcodes(post.content, path, repo_path, translations),
            )
    return pages


def slugify_heading(value: str) -> str:
    # Kubernetes uses Hugo's blackfriday anchor generation (punctuation -> '-').
    value = re.sub(r"[`*]", "", value.lower())
    value = re.sub(r"[^\w\s-]", "-", value, flags=re.UNICODE)
    return re.sub(r"[-\s]+", "-", value).strip("-")


def clean_markdown(text: str) -> str:
    def clean(prose: str) -> str:
        prose = HEADING_SHORTCODE_RE.sub(
            lambda match: match.group(1).replace("-", " ").title(), prose
        )
        prose = SHORTCODE_RE.sub(" ", prose)
        prose = re.sub(r"<!--.*?-->", " ", prose, flags=re.DOTALL)
        prose = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", prose)
        return re.sub(r"\n{3,}", "\n\n", prose)

    return _outside_code(text, clean).strip()


class Chunker:
    def __init__(self, budget: TokenBudget | None = None):
        self._budget = budget

    @property
    def budget(self) -> TokenBudget:
        if self._budget is None:
            from .config import get_settings

            settings = get_settings()
            self._budget = TokenBudget.from_cache(
                settings.model_cache_path, settings.local_embedding_model
            )
        return self._budget

    @staticmethod
    def _headings(text: str, title: str) -> list[tuple[int, int, str, str]]:
        headings = []
        hierarchy: dict[int, str] = {}
        used_anchors: set[str] = set()
        fence: str | None = None
        offset = 0
        for line in text.splitlines(keepends=True):
            fence_match = FENCE_RE.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if fence is None:
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence):
                    fence = None
            match = (
                HEADING_RE.match(line.rstrip()) if fence is None and fence_match is None else None
            )
            if match:
                level = len(match.group(1))
                heading = match.group(2).strip()
                explicit = EXPLICIT_ANCHOR_RE.search(heading)
                anchor = explicit.group(1) if explicit else slugify_heading(heading)
                if explicit:
                    heading = heading[: explicit.start()].rstrip()
                else:
                    base, suffix = anchor, 1
                    while anchor in used_anchors:
                        anchor = f"{base}-{suffix}"
                        suffix += 1
                used_anchors.add(anchor)
                hierarchy = {key: value for key, value in hierarchy.items() if key < level}
                hierarchy[level] = heading
                label = " > ".join(hierarchy[key] for key in sorted(hierarchy))
                headings.append((offset, offset + len(line), label, anchor))
            offset += len(line)
        return headings

    def heading_chunks(self, page: Page) -> list[dict]:
        cleaned = clean_markdown(page.raw_content)
        headings = self._headings(cleaned, page.title)
        sections = [(0, 0, page.title, ""), *headings]
        chunks: list[dict] = []
        for position, (_, body_start, heading, anchor) in enumerate(sections):
            body_end = sections[position + 1][0] if position + 1 < len(sections) else len(cleaned)
            body = cleaned[body_start:body_end].strip()
            prefix = f"# {page.title}\n## {heading}\n"
            for _, _, part in self.budget.windows(body, prefix):
                chunks.append(self._make_chunk(page, "heading", heading, part, len(chunks), anchor))
        return chunks

    def window_chunks(self, page: Page) -> list[dict]:
        cleaned = clean_markdown(page.raw_content)
        headings = self._headings(cleaned, page.title)
        positions = [item[0] for item in headings]
        chunks: list[dict] = []
        for index, (start, _, part) in enumerate(self.budget.windows(cleaned, f"# {page.title}\n")):
            preceding = bisect_right(positions, start) - 1
            heading, anchor = headings[preceding][2:] if preceding >= 0 else (page.title, "")
            chunks.append(self._make_chunk(page, "window", heading, part, index, anchor))
        return chunks

    def _make_chunk(
        self, page: Page, strategy: str, heading: str, content: str, index: int, anchor: str = ""
    ) -> dict:
        identity = f"{page.doc_id}:{strategy}:{index}:{hashlib.sha1(content.encode()).hexdigest()}"
        url = page.url + (f"#{anchor}" if anchor else "")
        return {
            "chunk_id": str(uuid.uuid5(K8S_NAMESPACE, identity)),
            "heading": heading,
            "content": content,
            "url": url,
            "embedding_tokens": self.budget.count(content),
        }

    def chunk(self, page: Page, strategy: Literal["heading", "window"]) -> list[dict]:
        return self.heading_chunks(page) if strategy == "heading" else self.window_chunks(page)
