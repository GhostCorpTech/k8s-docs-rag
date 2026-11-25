from app.retrieval import Retriever


def test_diversify_removes_duplicate_urls_and_caps_documents() -> None:
    candidates = [
        {"chunk_id": "1", "doc_id": "large", "url": "https://docs/page/#same"},
        {"chunk_id": "2", "doc_id": "large", "url": "https://docs/page/#same"},
        {"chunk_id": "3", "doc_id": "large", "url": "https://docs/page/#other"},
        {"chunk_id": "4", "doc_id": "large", "url": "https://docs/page/#third"},
        {"chunk_id": "5", "doc_id": "small", "url": "https://docs/other/"},
    ]

    result = Retriever._diversify(candidates)

    assert [item["chunk_id"] for item in result] == ["1", "3", "5"]


def test_normalize_metadata_uses_explicit_hugo_anchor() -> None:
    hit = {
        "heading": "Parent > Resource {#networkpolicy-resource}",
        "url": "https://kubernetes.io/docs/page/#resource-networkpolicy-resource",
    }

    result = Retriever._normalize_metadata(hit)

    assert result["heading"] == "Parent > Resource"
    assert result["url"] == "https://kubernetes.io/docs/page/#networkpolicy-resource"
