from pathlib import Path

from app.docs import _derive_url, clean_markdown, load_pages


def test_root_url_and_branch_slug() -> None:
    assert _derive_url(Path("_index.md"), {}) == "https://kubernetes.io/docs/"
    assert (
        _derive_url(Path("contribute/blog/_index.md"), {"slug": "other"})
        == "https://kubernetes.io/docs/contribute/blog/"
    )
    assert _derive_url(Path("contribute/blog/post.md"), {"slug": "other"}).endswith("/other/")


def test_code_samples_resolve_and_affect_incremental_hash(tmp_path: Path) -> None:
    docs = tmp_path / "content/en/docs"
    docs.mkdir(parents=True)
    source = docs / "example.md"
    source.write_text(
        '---\ntitle: Example\n---\n## Config\n{{% code_sample file="/pods/sample.yaml" %}}\n',
        encoding="utf-8",
    )
    sample = tmp_path / "content/en/examples/pods/sample.yaml"
    sample.parent.mkdir(parents=True)
    sample.write_text("kind: Pod\n", encoding="utf-8")
    old = load_pages(tmp_path)["example.md"]
    assert "kind: Pod" in old.raw_content
    sample.write_text("kind: Deployment\n", encoding="utf-8")
    assert old.content_hash != load_pages(tmp_path)["example.md"].content_hash


def test_shortcodes_inside_fenced_examples_are_not_expanded(tmp_path: Path) -> None:
    docs = tmp_path / "content/en/docs"
    docs.mkdir(parents=True)
    (docs / "example.md").write_text(
        '---\ntitle: Example\n---\n```\n{{% code_sample file="not-real.yaml" %}}\n```\n',
        encoding="utf-8",
    )
    page = load_pages(tmp_path)["example.md"]
    assert 'file="not-real.yaml"' in clean_markdown(page.raw_content)
