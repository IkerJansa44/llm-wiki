# LLM Wiki Maintainer

This repository is an Obsidian wiki maintained by an LLM with the user iterating on generated Markdown after ingestion.

Before wiki work, read:

1. `vault/wiki/_meta/conventions.md`
2. `vault/wiki/_meta/index.md`
3. The last 10 entries of `vault/wiki/_meta/log.md`

`vault/wiki/_meta/` is hidden in Obsidian and should only hold maintainer metadata. Keep the visible pending analysis queue at `vault/wiki/analysis-queue.md` so the user can see and edit it alongside the other wiki Markdown files. Keep the analysis queue graph-isolated: do not add Obsidian wikilinks to it, and do not link from it to wiki pages. Use plain text for likely connections inside the queue. Add items there when the user wants to save something for future analysis. When a queued item is analyzed and ingested, remove it from the queue; do not keep an ingested/completed archive in that file.

For user-provided content or source links, ingest directly instead of asking for permission or waiting for approval. Preserve the raw source, create or update the relevant wiki Markdown files, update the index and log, and then report what changed. The user will iterate on the created Markdown if they want revisions.

Keep links meaningful, cite important claims, and keep synthesized wiki content free of unresolved contradictions. If source claims conflict, do not silently resolve them; document the conflict in the affected Markdown and call it out in the final report.

Include visual content when it materially helps learning or understanding, especially for technical topics, conceptual explanations, research papers, systems, architectures, algorithms, charts, or source figures. Do not add decorative or locator visuals to simple personal records, administrative documents, queue entries, metadata pages, or other notes where the visual does not explain the content.

When a technical concept would be easier to understand visually, proactively generate or acquire a helpful image, diagram, or visual explanation and add it to the wiki. The user finds visuals especially useful for learning technical material.

When adding local images or diagrams to wiki pages, render them as Markdown image embeds with relative paths from the note instead of leaving bare file paths in code formatting.

When the user corrects the maintainer workflow or answer style, preserve the correction in these instructions or the wiki conventions so future sessions inherit it.

## Tooling

Use the repo virtual environment for Python tooling:

- Python: `.venv/bin/python`
- YouTube transcript fetcher: `.venv/bin/python scripts/youtube_transcript.py`
- PDF Docling extractor: `.venv/bin/python scripts/pdf_docling.py`
- `yt-dlp`: `.venv/bin/yt-dlp`

## Source Acquisition

For YouTube sources, use `.venv/bin/python scripts/youtube_transcript.py` to create immutable transcript files under `vault/raw/`. Use `--start` and `--end` when the user only wants a specific range.

For PDF sources, use `.venv/bin/python scripts/pdf_docling.py` to preserve the original PDF under `vault/raw/` and create a sibling `.docling.md` extraction for reading and citation work. Install Docling into the repo virtual environment if needed with `uv pip install --python .venv/bin/python docling`. Treat the PDF as the canonical immutable source and the Docling Markdown as a working extraction that must be checked against the PDF or official HTML for important claims, tables, formulas, and figures.

For ChatGPT shared conversations, normal consumer share links have this form:

```text
https://chatgpt.com/share/<conversation-id>
```

Treat the share page as source material. Prefer the in-app browser with the Browser plugin: open the URL, dismiss the cookie dialog if needed, confirm the page says it is a copy of a conversation, then extract the visible conversation into `vault/raw/` or a working transcript before ingesting it. If a generic web fetch fails, do not assume the link is private; `chatgpt.com/share/...` pages may still be readable in the in-app browser. If browser extraction is incomplete, ask for a ChatGPT data export and use `conversations.json`, or ask the user to paste the conversation text.
