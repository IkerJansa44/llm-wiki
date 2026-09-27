import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.import_daily_updates import import_chats


def create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE daily_summaries (
            id INTEGER PRIMARY KEY, run_date TEXT, title TEXT, plain_summary TEXT
        );
        CREATE TABLE summary_items (
            id INTEGER PRIMARY KEY, summary_id INTEGER, position INTEGER,
            title TEXT, url TEXT, source TEXT, author TEXT, published_at TEXT,
            why_it_matters TEXT, topics_json TEXT, verification_urls_json TEXT,
            signal_key TEXT
        );
        CREATE TABLE summary_chat_messages (
            id INTEGER PRIMARY KEY, item_id INTEGER, role TEXT, content TEXT,
            item_ids_json TEXT, created_at TEXT
        );
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY, run_date TEXT, question TEXT,
            choices_json TEXT, correct_choice_id TEXT, explanation TEXT,
            source_files_json TEXT, source_quotes_json TEXT, topic TEXT,
            difficulty TEXT, why_asked TEXT
        );
        CREATE TABLE question_chat_messages (
            id INTEGER PRIMARY KEY, question_id INTEGER, role TEXT,
            content TEXT, created_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO daily_summaries VALUES (1, '2026-09-01', 'Daily AI', 'Overview')"
    )
    connection.executemany(
        "INSERT INTO summary_items VALUES (?, 1, ?, ?, ?, 'web', '', '', ?, '[]', '[]', 'signal')",
        [
            (10, 1, "Primary source", "https://example.com/one", "Important"),
            (11, 2, "Related source", "https://example.com/two", "Corroborates"),
        ],
    )
    connection.executemany(
        "INSERT INTO summary_chat_messages VALUES (?, 10, ?, ?, ?, '2026-09-01 10:00:00')",
        [
            (100, "user", "Explain this", "[10, 11]"),
            (101, "assistant", "Explanation", "[10, 11]"),
            (102, "user", "Incomplete", "[10, 11]"),
        ],
    )
    connection.execute(
        """
        INSERT INTO questions VALUES (
            20, '2026-09-02', 'What is X?',
            '[{"id":"A","text":"Answer"}]', 'A', 'Because X',
            '["vault/wiki/topics/x.md"]', '["X quote"]',
            'X', 'easy', 'Review X'
        )
        """
    )
    connection.executemany(
        "INSERT INTO question_chat_messages VALUES (?, 20, ?, ?, '2026-09-02 11:00:00')",
        [(200, "user", "More detail"), (201, "assistant", "More explanation")],
    )
    connection.commit()
    connection.close()


def test_import_chats_writes_complete_immutable_turns(tmp_path: Path) -> None:
    database = tmp_path / "daily.sqlite3"
    output_dir = tmp_path / "raw"
    create_database(database)
    first = import_chats(database, output_dir)
    assert first["created_count"] == 2
    assert first["existing_count"] == 0
    assert first["warnings"] == [
        "summary:10: message 102 has no following assistant row"
    ]
    summary_path = output_dir / "2026-09-01-daily-updates-summary-chat-10-turn-100-101.md"
    question_path = output_dir / "2026-09-02-daily-updates-question-chat-20-turn-200-201.md"
    summary = summary_path.read_text(encoding="utf-8")
    question = question_path.read_text(encoding="utf-8")
    assert 'event_key: "summary:10:100:101"' in summary
    assert "item_ids: [10, 11]" in summary
    assert 'item_ids_source: "stored:item_ids_json"' in summary
    assert "### Item 11: Related source" in summary
    assert "### User\n\nExplain this" in summary
    assert 'event_key: "question:20:200:201"' in question
    assert "### Source quotes\n\n- X quote" in question
    second = import_chats(database, output_dir)
    assert second["created_count"] == 0
    assert second["existing_count"] == 2
    assert summary_path.read_text(encoding="utf-8") == summary
    assert question_path.read_text(encoding="utf-8") == question


def test_import_chats_reconstructs_item_ids_from_signal_key(tmp_path: Path) -> None:
    database = tmp_path / "daily.sqlite3"
    output_dir = tmp_path / "raw"
    create_database(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE summary_chat_messages SET item_ids_json = NULL")
    connection.commit()
    connection.close()
    import_chats(database, output_dir)
    summary = (
        output_dir / "2026-09-01-daily-updates-summary-chat-10-turn-100-101.md"
    ).read_text(encoding="utf-8")
    assert "item_ids: [10, 11]" in summary
    assert 'item_ids_source: "reconstructed:signal_key"' in summary
