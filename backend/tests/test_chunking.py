from app.docs import Chunker, Page


def sample_page() -> Page:
    return Page(
        doc_id="concepts/example.md",
        source_path="content/en/docs/concepts/example.md",
        url="https://kubernetes.io/docs/concepts/example/",
        title="Example",
        raw_content="""Intro text.

## First Section

First body.

### Nested Topic

Nested body.

## Second Section

Second body.

## {{% heading "generated-prerequisites" %}}

Generated heading body.
""",
    )


def test_heading_chunker_preserves_hierarchy_and_anchor(chunker: Chunker) -> None:
    chunks = chunker.heading_chunks(sample_page())
    nested = next(chunk for chunk in chunks if "Nested body" in chunk["content"])
    assert nested["heading"] == "First Section > Nested Topic"
    assert nested["url"].endswith("#nested-topic")
    second = next(chunk for chunk in chunks if "Second body" in chunk["content"])
    assert second["heading"] == "Second Section"
    generated = next(chunk for chunk in chunks if "Generated heading body" in chunk["content"])
    assert generated["heading"] == "Generated Prerequisites"


def test_window_chunker_returns_stable_metadata(chunker: Chunker) -> None:
    chunks = chunker.window_chunks(sample_page())
    assert chunks
    assert chunks[0]["chunk_id"]
    assert chunks[0]["url"].startswith("https://kubernetes.io/")


def test_all_chunks_fit_actual_embedding_input(chunker: Chunker) -> None:
    page = sample_page()
    page.raw_content = "## Dense YAML\n\n" + "metadata: {name: pod, labels: [a,b,c,d]}\n" * 150
    for strategy in ("heading", "window"):
        chunks = chunker.chunk(page, strategy)
        assert len(chunks) > 1
        assert all(chunker.budget.count(chunk["content"]) <= 512 for chunk in chunks)
        assert all(chunk["embedding_tokens"] <= 512 for chunk in chunks)
        assert all(chunk["content"].startswith("# Example\n") for chunk in chunks)


def test_explicit_duplicate_anchors_and_code_comments(chunker: Chunker) -> None:
    page = sample_page()
    page.raw_content = (
        "## Same\nA\n## Same\nB\n## Real {#custom}\n```yaml\n# Not a heading\nfoo: bar\n```\n"
    )
    chunks = chunker.heading_chunks(page)
    assert [chunk["url"].split("#")[-1] for chunk in chunks] == ["same", "same-1", "custom"]
    assert chunks[-1]["heading"] == "Real"
    assert "# Not a heading" in chunks[-1]["content"]


def test_window_retains_preceding_section(chunker: Chunker) -> None:
    page = sample_page()
    page.raw_content = "## Actual {#actual}\n\n" + "long sentence " * 200
    chunks = chunker.window_chunks(page)
    assert len(chunks) > 2
    assert all(chunk["url"].endswith("#actual") for chunk in chunks[1:])
