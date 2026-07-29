# Preflight Check

Run this script against a development database before migrations.

## Usage

```bash
export DATABASE_URL='postgresql://user:pass@localhost:5432/dev_db'
./.venv/bin/python sql/migrations/000_preflight_check.py
echo $?
```

## Phased behavior

### FASE 0

The script validates the base schema required to start migration `001_add_columns.sql`:

- required tables:
  - `teams`
  - `matches`
  - `events_timeline`
  - `raw_api_responses`
- required current columns only

If one of those tables or mandatory columns is missing, the script returns a blocking `ERROR`
and the migration must stop.

### PENDING columns

Columns created later by migration `001_add_columns.sql` are reported as:

- `PENDING — será creada por la migración 001`

If such a column does not exist yet, the script does **not** query it directly.
Instead, it reports the item as `PENDING — será creada por la migración 001`.

### Fases posteriores

If a later-phase column already exists, the script validates it:

- `response_hash`
- `teams.espn_team_id`
- `events_timeline.event_ledger_id`
- other later-phase columns

If they do not exist yet, they remain `PENDING`.

## Exit codes

- `0`: the base schema allows starting migration `001_add_columns.sql`
- `1`: blocking schema/data inconsistency
- `2`: missing `DATABASE_URL`
- `3`: database connection or query failure
- `4`: unexpected preflight failure
- non-zero: the migration should stop

## Read-only guarantee

- Only `SELECT` queries are executed.
- The script does not execute `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `CREATE`, `DROP`, or `TRUNCATE`.
- All queries are parameterized.

## Report labels

- `OK`
- `WARNING`
- `PENDING`
- `ERROR`

## Query safety

- The script first checks table and column existence through `information_schema`.
- It only queries optional columns after confirming they exist.
- It never issues writes of any kind.
