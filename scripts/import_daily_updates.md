# Daily Updates conversation importer

Run from the repository root:

```bash
python3 scripts/import_daily_updates.py
```

The importer opens SQLite read-only and exports every newly completed Daily
Updates or quiz-question chat turn as an immutable Markdown source under
`vault/raw/`. Stable filenames and event keys make reruns idempotent; existing
raw sources are never rewritten. Incomplete turns are reported and skipped.

The default source is
`/home/iker/Documents/daily-updates/data/daily_updates.sqlite3`. Override it with
`--database PATH` or the `DAILY_UPDATES_DB` environment variable. `--dry-run`
reports the import without writing files. `--manifest PATH` writes the JSON run
result for a subsequent Codex synthesis step.

For summary chats, exact context is read from any of these message columns when
present:

- `item_ids`
- `item_ids_json`
- `context_item_ids`
- `context_item_ids_json`

Older schemas are supported by reconstructing the context from the parent
summary and `signal_key`. Every raw source records whether its context IDs were
stored, reconstructed, or reduced to the primary item.
