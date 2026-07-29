# Manual Rollback Notes

This project uses a non-destructive rollback policy.

## What rollback means here

- Do not delete existing data automatically.
- Do not drop tables automatically.
- Do not drop columns automatically.
- Only remove objects that were added by the migration when you have explicit confirmation.

## Safe rollback steps

1. Stop the deployment or migration runner.
2. Review verification queries from `004_verify.sql`.
3. Resolve conflicts manually.
4. If a structural revert is absolutely required, prepare a dedicated rollback script and review it before execution.

## Recommended manual rollback candidates

- Unique indexes created in `003_create_indexes.sql`
- Foreign key on `events_timeline.event_ledger_id`
- Newly added columns if and only if a full revert is explicitly approved

## Not allowed by default

- Automatic `DROP TABLE`
- Automatic `DROP COLUMN`
- Automatic data deletion

