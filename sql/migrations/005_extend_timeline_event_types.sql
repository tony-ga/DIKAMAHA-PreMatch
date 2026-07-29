BEGIN;

-- =========================================================
-- 005_extend_timeline_event_types.sql
-- Extiende el CHECK de events_timeline para admitir shot_blocked.
-- =========================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'events_timeline'
    ) THEN
        RAISE EXCEPTION 'La tabla events_timeline no existe';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'events_timeline'
          AND column_name = 'event_type'
    ) THEN
        RAISE EXCEPTION 'La columna events_timeline.event_type no existe';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM events_timeline
        WHERE event_type NOT IN (
            'goal',
            'shot_on_target',
            'shot_off_target',
            'shot_blocked',
            'corner',
            'foul',
            'yellow',
            'red',
            'substitution'
        )
    ) THEN
        RAISE EXCEPTION 'Existen filas incompatibles con el nuevo CHECK en events_timeline.event_type';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = c.connamespace
        WHERE n.nspname = 'public'
          AND t.relname = 'events_timeline'
          AND c.conname = 'chk_events_event_type'
    ) THEN
        ALTER TABLE events_timeline
            DROP CONSTRAINT chk_events_event_type;
    END IF;

    ALTER TABLE events_timeline
        ADD CONSTRAINT chk_events_event_type
        CHECK (
            event_type IN (
                'goal',
                'shot_on_target',
                'shot_off_target',
                'shot_blocked',
                'corner',
                'foul',
                'yellow',
                'red',
                'substitution'
            )
        );
END $$;

COMMIT;

-- Verificación posterior:
-- SELECT pg_get_constraintdef(c.oid)
-- FROM pg_constraint c
-- JOIN pg_class t ON t.oid = c.conrelid
-- WHERE t.relname = 'events_timeline' AND c.conname = 'chk_events_event_type';
