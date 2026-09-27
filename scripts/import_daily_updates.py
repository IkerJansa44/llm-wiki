#!/usr/bin/env python3
"""Export completed Daily Updates chat turns as immutable wiki raw sources."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = Path(
    os.environ.get(
        "DAILY_UPDATES_DB",
        "/home/iker/Documents/daily-updates/data/daily_updates.sqlite3",
    )
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "vault/raw"
ITEM_IDS_COLUMNS = (
    "item_ids_json",
    "context_item_ids_json",
    "item_ids",
    "context_item_ids",
)


@dataclass(frozen=True)
class ChatTurn:
    user: sqlite3.Row
    assistant: sqlite3.Row


def yaml_scalar(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_value(value: object, default: object) -> object:
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def markdown_text(value: object) -> str:
    text = str(value or "").strip()
    return text or "_Not recorded._"


def connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Daily Updates database not found: {path}")
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def require_schema(connection: sqlite3.Connection) -> None:
    required = {
        "daily_summaries": {"id", "run_date", "title", "plain_summary"},
        "summary_items": {"id", "summary_id", "title", "url", "signal_key"},
        "summary_chat_messages": {"id", "item_id", "role", "content", "created_at"},
        "questions": {
            "id", "run_date", "question", "choices_json", "correct_choice_id",
            "explanation", "source_files_json", "source_quotes_json", "topic",
            "difficulty", "why_asked",
        },
        "question_chat_messages": {
            "id", "question_id", "role", "content", "created_at",
        },
    }
    problems: list[str] = []
    for table, expected in required.items():
        missing = sorted(expected - table_columns(connection, table))
        if missing:
            problems.append(f"{table} is missing: {', '.join(missing)}")
    if problems:
        raise RuntimeError("Unsupported Daily Updates schema: " + "; ".join(problems))


def pair_turns(
    rows: list[sqlite3.Row], stream: str, parent_id: int
) -> tuple[list[ChatTurn], list[str]]:
    turns: list[ChatTurn] = []
    warnings: list[str] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        if row["role"] != "user":
            warnings.append(
                f"{stream}:{parent_id}: message {row['id']} is an unpaired assistant row"
            )
            index += 1
            continue
        if index + 1 >= len(rows) or rows[index + 1]["role"] != "assistant":
            warnings.append(
                f"{stream}:{parent_id}: message {row['id']} has no following assistant row"
            )
            index += 1
            continue
        turns.append(ChatTurn(row, rows[index + 1]))
        index += 2
    return turns, warnings


def parse_item_ids(value: object, primary_id: int) -> list[int]:
    if value is None or value == "":
        return []
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(parsed, list):
        return []
    result: list[int] = []
    for candidate in parsed:
        try:
            item_id = int(candidate)
        except (TypeError, ValueError):
            continue
        if item_id not in result:
            result.append(item_id)
    if primary_id not in result:
        result.insert(0, primary_id)
    return result


def summary_item_ids(
    connection: sqlite3.Connection,
    turn: ChatTurn,
    primary: sqlite3.Row,
) -> tuple[list[int], str]:
    keys = set(turn.user.keys())
    for column in ITEM_IDS_COLUMNS:
        if column in keys:
            for row in (turn.user, turn.assistant):
                parsed = parse_item_ids(row[column], int(primary["id"]))
                if parsed:
                    return parsed, f"stored:{column}"

    signal_key = str(primary["signal_key"] or "").strip()
    if signal_key:
        ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM summary_items
                WHERE summary_id = ? AND signal_key = ?
                ORDER BY position, id
                """,
                (primary["summary_id"], signal_key),
            )
        ]
        if ids:
            return ids, "reconstructed:signal_key"
    return [int(primary["id"])], "fallback:primary_only"


def rows_by_id(
    connection: sqlite3.Connection, table: str, ids: list[int]
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in ids)
    found = {
        int(row["id"]): row
        for row in connection.execute(
            f"SELECT * FROM {table} WHERE id IN ({placeholders})", ids
        )
    }
    return [found[item_id] for item_id in ids if item_id in found]


def source_item_markdown(item: sqlite3.Row) -> str:
    topics = json_value(item["topics_json"], []) if "topics_json" in item.keys() else []
    verification_urls = (
        json_value(item["verification_urls_json"], [])
        if "verification_urls_json" in item.keys()
        else []
    )
    lines = [
        f"### Item {item['id']}: {markdown_text(item['title'])}",
        "",
        f"- URL: {markdown_text(item['url'])}",
        f"- Source: {markdown_text(item['source']) if 'source' in item.keys() else '_Not recorded._'}",
        f"- Author: {markdown_text(item['author']) if 'author' in item.keys() else '_Not recorded._'}",
        f"- Published: {markdown_text(item['published_at']) if 'published_at' in item.keys() else '_Not recorded._'}",
        f"- Topics: {', '.join(map(str, topics)) if isinstance(topics, list) and topics else '_None recorded._'}",
        f"- Why it matters: {markdown_text(item['why_it_matters']) if 'why_it_matters' in item.keys() else '_Not recorded._'}",
    ]
    if isinstance(verification_urls, list) and verification_urls:
        lines.extend(["- Verification URLs:", *[f"  - {url}" for url in verification_urls]])
    return "\n".join(lines)


def render_summary_turn(
    database: Path,
    imported_at: str,
    turn: ChatTurn,
    summary: sqlite3.Row,
    primary: sqlite3.Row,
    items: list[sqlite3.Row],
    item_ids: list[int],
    item_ids_source: str,
) -> str:
    event_key = f"summary:{primary['id']}:{turn.user['id']}:{turn.assistant['id']}"
    sections = [
        "---", "source_type: daily-updates-chat", "stream: summary",
        f"event_key: {yaml_scalar(event_key)}",
        f"source_database: {yaml_scalar(str(database.resolve()))}",
        f"run_date: {yaml_scalar(summary['run_date'])}",
        f"parent_item_id: {primary['id']}",
        f"user_message_id: {turn.user['id']}",
        f"assistant_message_id: {turn.assistant['id']}",
        f"item_ids: {json.dumps(item_ids)}",
        f"item_ids_source: {yaml_scalar(item_ids_source)}",
        f"created_at: {yaml_scalar(turn.user['created_at'])}",
        f"imported_at: {yaml_scalar(imported_at)}", "---", "",
        f"# Daily Updates conversation: {markdown_text(primary['title'])}", "",
        "## Daily update context", "",
        f"- Title: {markdown_text(summary['title'])}",
        f"- Run date: {markdown_text(summary['run_date'])}",
        f"- Primary item ID: {primary['id']}",
        f"- Context item IDs: {', '.join(map(str, item_ids))}",
        f"- Context-ID source: `{item_ids_source}`", "",
        markdown_text(summary["plain_summary"]), "", "## Context items", "",
        "\n\n".join(source_item_markdown(item) for item in items), "",
        "## Conversation", "", "### User", "",
        markdown_text(turn.user["content"]), "", "### Assistant", "",
        markdown_text(turn.assistant["content"]), "",
    ]
    return "\n".join(sections)


def list_markdown(value: object) -> str:
    parsed = json_value(value, [])
    if not isinstance(parsed, list) or not parsed:
        return "_None recorded._"
    return "\n".join(f"- {item}" for item in parsed)


def choices_markdown(question: sqlite3.Row) -> str:
    choices = json_value(question["choices_json"], [])
    if not isinstance(choices, list):
        return "_Not recorded._"
    lines = [
        f"- {choice.get('id', '?')}: {choice.get('text', '')}"
        if isinstance(choice, dict) else f"- {choice}"
        for choice in choices
    ]
    return "\n".join(lines) or "_Not recorded._"


def render_question_turn(
    database: Path,
    imported_at: str,
    turn: ChatTurn,
    question: sqlite3.Row,
) -> str:
    event_key = f"question:{question['id']}:{turn.user['id']}:{turn.assistant['id']}"
    sections = [
        "---", "source_type: daily-updates-chat", "stream: question",
        f"event_key: {yaml_scalar(event_key)}",
        f"source_database: {yaml_scalar(str(database.resolve()))}",
        f"run_date: {yaml_scalar(question['run_date'])}",
        f"parent_question_id: {question['id']}",
        f"user_message_id: {turn.user['id']}",
        f"assistant_message_id: {turn.assistant['id']}",
        f"created_at: {yaml_scalar(turn.user['created_at'])}",
        f"imported_at: {yaml_scalar(imported_at)}", "---", "",
        f"# Daily Updates question conversation: {markdown_text(question['topic'])}", "",
        "## Question context", "", markdown_text(question["question"]), "",
        "### Choices", "", choices_markdown(question), "",
        f"- Correct choice: {markdown_text(question['correct_choice_id'])}",
        f"- Topic: {markdown_text(question['topic'])}",
        f"- Difficulty: {markdown_text(question['difficulty'])}",
        f"- Why asked: {markdown_text(question['why_asked'])}", "",
        "### Original explanation", "", markdown_text(question["explanation"]), "",
        "### Source files", "", list_markdown(question["source_files_json"]), "",
        "### Source quotes", "", list_markdown(question["source_quotes_json"]), "",
        "## Conversation", "", "### User", "", markdown_text(turn.user["content"]),
        "", "### Assistant", "", markdown_text(turn.assistant["content"]), "",
    ]
    return "\n".join(sections)


def output_path(
    output_dir: Path, stream: str, run_date: str, parent_id: int,
    user_id: int, assistant_id: int,
) -> Path:
    return output_dir / (
        f"{run_date}-daily-updates-{stream}-chat-"
        f"{parent_id}-turn-{user_id}-{assistant_id}.md"
    )


def import_summary_turns(
    connection: sqlite3.Connection, database: Path, output_dir: Path,
    imported_at: str, dry_run: bool,
) -> tuple[list[Path], int, list[str]]:
    grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in connection.execute("SELECT * FROM summary_chat_messages ORDER BY item_id, id"):
        grouped[int(row["item_id"])].append(row)
    created: list[Path] = []
    existing = 0
    warnings: list[str] = []
    for item_id, messages in grouped.items():
        primary = connection.execute(
            "SELECT * FROM summary_items WHERE id = ?", (item_id,)
        ).fetchone()
        if primary is None:
            warnings.append(f"summary:{item_id}: missing parent summary_items row")
            continue
        summary = connection.execute(
            "SELECT * FROM daily_summaries WHERE id = ?", (primary["summary_id"],)
        ).fetchone()
        if summary is None:
            warnings.append(f"summary:{item_id}: missing parent daily_summaries row")
            continue
        turns, turn_warnings = pair_turns(messages, "summary", item_id)
        warnings.extend(turn_warnings)
        for turn in turns:
            path = output_path(
                output_dir, "summary", str(summary["run_date"]), item_id,
                int(turn.user["id"]), int(turn.assistant["id"]),
            )
            if path.exists():
                existing += 1
                continue
            item_ids, ids_source = summary_item_ids(connection, turn, primary)
            content = render_summary_turn(
                database, imported_at, turn, summary, primary,
                rows_by_id(connection, "summary_items", item_ids),
                item_ids, ids_source,
            )
            if not dry_run:
                path.write_text(content, encoding="utf-8")
            created.append(path)
    return created, existing, warnings


def import_question_turns(
    connection: sqlite3.Connection, database: Path, output_dir: Path,
    imported_at: str, dry_run: bool,
) -> tuple[list[Path], int, list[str]]:
    grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in connection.execute("SELECT * FROM question_chat_messages ORDER BY question_id, id"):
        grouped[int(row["question_id"])].append(row)
    created: list[Path] = []
    existing = 0
    warnings: list[str] = []
    for question_id, messages in grouped.items():
        question = connection.execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if question is None:
            warnings.append(f"question:{question_id}: missing parent questions row")
            continue
        turns, turn_warnings = pair_turns(messages, "question", question_id)
        warnings.extend(turn_warnings)
        for turn in turns:
            path = output_path(
                output_dir, "question", str(question["run_date"]), question_id,
                int(turn.user["id"]), int(turn.assistant["id"]),
            )
            if path.exists():
                existing += 1
                continue
            if not dry_run:
                path.write_text(
                    render_question_turn(database, imported_at, turn, question),
                    encoding="utf-8",
                )
            created.append(path)
    return created, existing, warnings


def import_chats(
    database: Path, output_dir: Path, dry_run: bool = False,
) -> dict[str, object]:
    imported_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    with connect_read_only(database) as connection:
        require_schema(connection)
        summary = import_summary_turns(
            connection, database, output_dir, imported_at, dry_run
        )
        questions = import_question_turns(
            connection, database, output_dir, imported_at, dry_run
        )
    created = summary[0] + questions[0]
    return {
        "schema_version": 1,
        "database": str(database.resolve()),
        "output_dir": str(output_dir.resolve()),
        "imported_at": imported_at,
        "dry_run": dry_run,
        "created_count": len(created),
        "existing_count": summary[1] + questions[1],
        "created_paths": [str(path.resolve()) for path in created],
        "warnings": summary[2] + questions[2],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = import_chats(args.database, args.output_dir, args.dry_run)
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as error:
        raise SystemExit(str(error)) from error
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.manifest and not args.dry_run:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
