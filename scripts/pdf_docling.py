#!/usr/bin/env python3
"""Download or copy a PDF source and convert it to markdown with Docling."""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


DEFAULT_OUTPUT_DIR = Path("vault/raw")
DEFAULT_TIMEOUT = 60


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "pdf-source"


def source_name(source: str) -> str:
    parsed = urlparse(source)
    if parsed.scheme and parsed.path:
        name = Path(parsed.path).stem
        if "arxiv.org" in parsed.netloc and parsed.path.startswith("/pdf/"):
            return f"arxiv-{name}"
        return name
    return Path(source).stem


def source_url(source: str) -> str | None:
    parsed = urlparse(source)
    return source if parsed.scheme in {"http", "https"} else None


def output_stem(args: argparse.Namespace) -> str:
    day = args.source_date or date.today().isoformat()
    return f"{day}-{slugify(args.title or source_name(args.source))}"


def fetch_source(source: str, pdf_path: Path, force: bool) -> None:
    if pdf_path.exists() and not force:
        return

    url = source_url(source)
    if url:
        with urlopen(url, timeout=DEFAULT_TIMEOUT) as response:
            pdf_path.write_bytes(response.read())
        return

    local_path = Path(source)
    if not local_path.exists():
        raise SystemExit(f"PDF not found: {local_path}")
    shutil.copyfile(local_path, pdf_path)


def convert_pdf(pdf_path: Path) -> str:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise SystemExit(
            "Docling is required. Install it with "
            "`uv pip install --python .venv/bin/python docling`, then rerun this command."
        ) from exc

    result = DocumentConverter().convert(pdf_path)
    return result.document.export_to_markdown()


def render_markdown(args: argparse.Namespace, pdf_path: Path, body: str) -> str:
    url = source_url(args.source)
    title = args.title or source_name(args.source)
    lines = [
        "---",
        "type: raw_source",
        "source_type: pdf_docling",
        f"source_id: {pdf_path.stem}",
        f"pdf_path: {pdf_path.as_posix()}",
        f"date_added: {date.today().isoformat()}",
    ]
    if args.source_date:
        lines.append(f"source_date: {args.source_date}")
    if url:
        lines.append(f"url: {url}")
    lines.extend(["---", "", f"# {title}", ""])
    if url:
        lines.extend([f"- URL: {url}", f"- PDF: {pdf_path.as_posix()}", ""])
    lines.extend(["## Docling Markdown", "", body.strip(), ""])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preserve a PDF in vault/raw and convert it to markdown with Docling."
    )
    parser.add_argument("source", help="PDF URL or local PDF path")
    parser.add_argument("--title", help="Human-readable title used for the output slug")
    parser.add_argument(
        "--source-date",
        help="Stable source date for the filename/frontmatter, e.g. 2025-08-08",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing PDF and markdown files"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    stem = output_stem(args)
    pdf_path = args.output_dir / f"{stem}.pdf"
    md_path = args.output_dir / f"{stem}.docling.md"
    if md_path.exists() and not args.force:
        raise SystemExit(f"Output already exists: {md_path}. Use --force to overwrite.")

    fetch_source(args.source, pdf_path, args.force)
    md_path.write_text(
        render_markdown(args, pdf_path, convert_pdf(pdf_path)),
        encoding="utf-8",
    )
    print(pdf_path)
    print(md_path)


if __name__ == "__main__":
    main()
