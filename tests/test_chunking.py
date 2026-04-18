from pdf2md import pipeline
from pdf2md.pipeline import Chapter, _build_chunks_for_chapter, _split_markdown_blocks


def test_split_markdown_blocks_preserves_tables_and_code() -> None:
    markdown = """
# Chapter 1

Intro paragraph.

| Col A | Col B |
| --- | --- |
| 1 | 2 |

```python
print("hello")
```
""".strip()

    blocks = _split_markdown_blocks(markdown, "Chapter 1")
    kinds = [block.kind for block in blocks]

    assert "table" in kinds
    assert "code" in kinds
    assert blocks[0].kind == "heading"


def test_chunk_builder_keeps_overlap_and_marks_oversize() -> None:
    markdown = """
# Chapter 1

This is sentence one. This is sentence two. This is sentence three. This is sentence four.

## Section A

Another paragraph here. More details follow. Even more detail appears now.

| Name | Value |
| --- | --- |
| alpha | beta |
""".strip()
    chapter = Chapter(
        index=1,
        title="Chapter 1",
        slug="chapter-1",
        markdown=markdown,
        page_start=1,
        page_end=2,
        origin="heading",
    )

    chunks = _build_chunks_for_chapter(chapter, chunk_target=30, chunk_overlap=10)

    assert len(chunks) >= 2
    assert chunks[0].breadcrumb[0] == "Chapter 1"
    assert all(chunk.text.startswith("_Path:") for chunk in chunks)
    assert all("_Pages:" in chunk.text for chunk in chunks)


def test_clean_markdown_document_rewraps_text_and_drops_page_markers() -> None:
    messy = """
Report title

This is a bro-
ken paragraph that spans
multiple lines.

Page 12
""".strip()

    cleaned = pipeline._clean_markdown_document(messy, aggressive=True)

    assert "broken paragraph that spans multiple lines." in cleaned
    assert "Page 12" not in cleaned


def test_tokenizer_falls_back_to_offline_approximation(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_TOKENIZER_CACHE", None)

    def fail_encoding(_: str):
        raise RuntimeError("offline")

    monkeypatch.setattr(pipeline.tiktoken, "get_encoding", fail_encoding)

    tokenizer = pipeline._get_tokenizer()
    pieces = pipeline._split_text_by_tokens("alpha beta gamma delta", 2)

    assert tokenizer.approximate is True
    assert tokenizer.name == "approx-wordpieces-v1"
    assert pieces == ["alpha beta", "gamma delta"]
