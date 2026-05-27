#!/usr/bin/env python3
"""Fetch a YouTube transcript and save a ranged markdown source."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse


TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
DEFAULT_OUTPUT_DIR = Path("vault/raw")
DEFAULT_SUB_LANGS = "en-US,en-orig,en"
REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_YTDLP = REPO_ROOT / ".venv/bin/yt-dlp"


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Chapter:
    start: float
    end: float | None
    title: str


def parse_time(value: str | None) -> float | None:
    if value is None:
        return None

    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"Invalid time: {value}")

    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def format_time(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_vtt_time(value: str) -> float:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def video_id(url_or_id: str) -> str:
    parsed = urlparse(url_or_id)
    if parsed.netloc in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/")
    if "youtube.com" in parsed.netloc:
        query_id = parse_qs(parsed.query).get("v")
        if query_id:
            return query_id[0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]
    return url_or_id


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "youtube-video"


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def require_ytdlp() -> str:
    if REPO_YTDLP.exists():
        return str(REPO_YTDLP)

    executable = shutil.which("yt-dlp")
    if executable:
        return executable
    raise SystemExit(
        "yt-dlp is required. Install it with `python3 -m pip install yt-dlp` "
        "or `brew install yt-dlp`, then rerun this command."
    )


def with_ytdlp_runtime(command: list[str]) -> list[str]:
    node = shutil.which("node")
    if not node:
        return command
    return [
        command[0],
        "--js-runtimes",
        f"node:{node}",
        "--remote-components",
        "ejs:github",
        *command[1:],
    ]


def with_cookies(command: list[str], cookies_from_browser: str | None) -> list[str]:
    if not cookies_from_browser:
        return command
    return [command[0], "--cookies-from-browser", cookies_from_browser, *command[1:]]


def fetch_title(
    ytdlp: str, url: str, cwd: Path, cookies_from_browser: str | None
) -> str:
    command = with_cookies(
        with_ytdlp_runtime([ytdlp, "--print", "title", "--no-playlist", url]),
        cookies_from_browser,
    )
    result = run(command, cwd)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()[0]
    return "YouTube Video"


def parse_chapters(metadata: dict[str, object]) -> list[Chapter]:
    chapters = metadata.get("chapters")
    if not isinstance(chapters, list):
        return []

    parsed: list[Chapter] = []
    for item in chapters:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        start = item.get("start_time")
        if not title or not isinstance(start, int | float):
            continue

        end = item.get("end_time")
        parsed.append(
            Chapter(
                start=float(start),
                end=float(end) if isinstance(end, int | float) else None,
                title=title,
            )
        )
    return parsed


def fetch_chapters(
    ytdlp: str, url: str, cwd: Path, cookies_from_browser: str | None
) -> list[Chapter]:
    command = with_cookies(
        with_ytdlp_runtime(
            [ytdlp, "--dump-single-json", "--skip-download", "--no-playlist", url]
        ),
        cookies_from_browser,
    )
    result = run(command, cwd)
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        return parse_chapters(json.loads(result.stdout))
    except json.JSONDecodeError:
        return []


def fetch_vtt(
    ytdlp: str,
    url: str,
    tmpdir: Path,
    cookies_from_browser: str | None,
    sub_langs: str,
) -> Path:
    output = tmpdir / "transcript"
    command = [
        ytdlp,
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        sub_langs,
        "--sub-format",
        "vtt",
        "--no-playlist",
        "--output",
        str(output),
        url,
    ]
    command = with_cookies(with_ytdlp_runtime(command), cookies_from_browser)

    result = run(command, tmpdir)
    vtt_files = sorted(tmpdir.glob("*.vtt"))
    if result.returncode != 0 or not vtt_files:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"Could not fetch transcript. yt-dlp said:\n{detail}")
    return vtt_files[0]


def clean_text(lines: list[str]) -> str:
    text = " ".join(line.strip() for line in lines if line.strip())
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_vtt(path: Path) -> list[Cue]:
    cues: list[Cue] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        match = TIMESTAMP_RE.search(lines[index])
        if not match:
            index += 1
            continue

        start = parse_vtt_time(match.group("start"))
        end = parse_vtt_time(match.group("end"))
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index])
            index += 1

        text = clean_text(text_lines)
        if text:
            cues.append(Cue(start=start, end=end, text=text))
    return dedupe_cues(cues)


def dedupe_cues(cues: list[Cue]) -> list[Cue]:
    deduped: list[Cue] = []
    previous = ""
    for cue in cues:
        if cue.text != previous:
            deduped.append(cue)
        previous = cue.text
    return deduped


def slice_cues(cues: list[Cue], start: float | None, end: float | None) -> list[Cue]:
    return [
        cue
        for cue in cues
        if (start is None or cue.start >= start) and (end is None or cue.start <= end)
    ]


def chapter_overlaps(chapter: Chapter, start: float | None, end: float | None) -> bool:
    range_start = start or 0
    range_end = end if end is not None else float("inf")
    chapter_end = chapter.end if chapter.end is not None else float("inf")
    return chapter.start <= range_end and chapter_end >= range_start


def slice_chapters(
    chapters: list[Chapter], start: float | None, end: float | None
) -> list[Chapter]:
    return [chapter for chapter in chapters if chapter_overlaps(chapter, start, end)]


def cue_chapter(cue: Cue, chapters: list[Chapter]) -> Chapter | None:
    for chapter in reversed(chapters):
        chapter_end = chapter.end if chapter.end is not None else float("inf")
        if chapter.start <= cue.start < chapter_end:
            return chapter
    return None


def render_chapter_list(lines: list[str], chapters: list[Chapter]) -> None:
    lines.extend(["## Chapters", ""])
    for chapter in chapters:
        lines.append(f"- [{format_time(chapter.start)}] {chapter.title}")
    lines.append("")


def render_transcript(
    lines: list[str], cues: list[Cue], chapters: list[Chapter]
) -> None:
    current_chapter: Chapter | None = None
    for cue in cues:
        next_chapter = cue_chapter(cue, chapters)
        if next_chapter and next_chapter != current_chapter:
            lines.extend(["", f"### {next_chapter.title}", ""])
            current_chapter = next_chapter
        lines.append(f"[{format_time(cue.start)}] {cue.text}")


def render_markdown(
    *,
    title: str,
    url: str,
    source_id: str,
    cues: list[Cue],
    start: float | None,
    end: float | None,
    chapters: list[Chapter] | None = None,
) -> str:
    chapters = chapters or []
    range_label = "full video"
    if start is not None or end is not None:
        range_end = end or cues[-1].end if cues else 0
        range_label = f"{format_time(start or 0)}-{format_time(range_end)}"

    lines = [
        "---",
        "type: raw_source",
        "source_type: youtube_transcript",
        f"source_id: {source_id}",
        f"url: {url}",
        f"date_added: {date.today().isoformat()}",
        f"range: {range_label}",
        "---",
        "",
        f"# {title}",
        "",
        f"- URL: {url}",
        f"- Range: {range_label}",
        "",
        "## Transcript",
        "",
    ]

    if chapters:
        lines.pop()
        lines.pop()
        render_chapter_list(lines, chapters)
        lines.extend(["## Transcript", ""])
        render_transcript(lines, cues, chapters)
    else:
        render_transcript(lines, cues, chapters)
    return "\n".join(lines).strip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a YouTube transcript into vault/raw, optionally restricted "
            "to a time range."
        )
    )
    parser.add_argument("url", help="YouTube URL or video ID")
    parser.add_argument("--start", help="Start time, e.g. 90, 01:30, or 00:01:30")
    parser.add_argument("--end", help="End time, e.g. 300, 05:00, or 00:05:00")
    parser.add_argument("--title", help="Override the fetched video title")
    parser.add_argument(
        "--sub-langs",
        default=DEFAULT_SUB_LANGS,
        help=f"yt-dlp subtitle languages, default: {DEFAULT_SUB_LANGS}",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--cookies-from-browser",
        help="Browser name for yt-dlp cookies, e.g. safari, chrome, or firefox",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing output file"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = parse_time(args.start)
    end = parse_time(args.end)
    if start is not None and end is not None and start > end:
        raise SystemExit("--start must be before --end")

    ytdlp = require_ytdlp()
    url = args.url
    source_id = video_id(url)
    title = args.title or fetch_title(ytdlp, url, Path.cwd(), args.cookies_from_browser)
    chapters = slice_chapters(
        fetch_chapters(ytdlp, url, Path.cwd(), args.cookies_from_browser), start, end
    )

    with tempfile.TemporaryDirectory(prefix="youtube-transcript-") as tmp:
        vtt_path = fetch_vtt(
            ytdlp,
            url,
            Path(tmp),
            args.cookies_from_browser,
            args.sub_langs,
        )
        cues = slice_cues(parse_vtt(vtt_path), start, end)

    if not cues:
        raise SystemExit("No transcript cues found for the requested range.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    range_suffix = ""
    if start is not None or end is not None:
        suffix_start = format_time(start or 0).replace(":", "")
        suffix_end = format_time(end or cues[-1].end).replace(":", "")
        range_suffix = f"-{suffix_start}-{suffix_end}"
    output_path = (
        args.output_dir
        / f"{date.today().isoformat()}-{slugify(title)}{range_suffix}.md"
    )
    if output_path.exists() and not args.force:
        raise SystemExit(
            f"Output already exists: {output_path}. Use --force to overwrite."
        )

    output_path.write_text(
        render_markdown(
            title=title,
            url=url,
            source_id=source_id,
            cues=cues,
            start=start,
            end=end,
            chapters=chapters,
        ),
        encoding="utf-8",
    )
    print(output_path)


if __name__ == "__main__":
    main()
