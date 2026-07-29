# Migration Plan

This migration set is safe, incremental, and non-destructive.

## Order of execution

1. `001_add_columns.sql`
2. `002_backfill_hashes.py`
3. `003_create_indexes.sql`
4. `004_verify.sql`

## Execution rules

- Do not run `003_create_indexes.sql` before `002_backfill_hashes.py`.
- Do not create unique indexes until:
  - `raw_api_responses.response_hash` has no NULLs.
  - `events_ledger.content_hash` has no NULLs.
  - duplicates have been checked and reported.
- Do not set `events_timeline.event_ledger_id` to NOT NULL yet.
- `teams.id` stays internal.
- `teams.espn_team_id` stores the ESPN identifier.

## Resolution flow

ESPN team ID -> `teams.espn_team_id` -> `teams.id` internal

Use this mapping in ingestion code for:

- `matches.home_team_id`
- `matches.away_team_id`

If an ESPN team id cannot be mapped:

- do not insert it into an internal foreign key;
- keep the internal FK as NULL where allowed;
- log a warning;
- report unmapped ids before any constraint hardening.

## Duplicate policy

- Do not delete rows automatically.
- Stop the migration if duplicate hashes are detected.
- Resolve conflicts manually before creating unique indexes.

## Rollback

Rollback is manual and non-destructive by default.
Do not drop columns or tables automatically.
If you need a destructive rollback, prepare it explicitly and review it first.

## Dependencies

- `sqlalchemy`
- `python-dotenv`
- `psycopg2-binary`

The Python backfill script computes hashes in Python using canonical JSON plus SHA-256.
No PostgreSQL extension is required for hashing.
