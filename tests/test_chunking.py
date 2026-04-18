from pdf2md.pipeline import _build_chunks_for_chapter, _split_markdown_blocks, Chapter


def test_split_markdown_blocks_preserves_tables_and_code():
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


def test_chunk_builder_keeps_overlap_and_marks_oversize():
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
