-- A5: Mükerrer arşiv batch audit / geri alma
-- customers finans tablolarına dokunmaz; yalnızca arşiv meta + bu log tablosu.

CREATE TABLE IF NOT EXISTS mukerrer_arsiv_batch (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    group_key     TEXT NOT NULL,
    tier          TEXT,
    kanonik_id    INTEGER NOT NULL,
    archived_ids  INTEGER[] NOT NULL,
    payload_json  JSONB,
    undone_at     TIMESTAMPTZ,
    undone_by     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_mukerrer_arsiv_batch_created_at
    ON mukerrer_arsiv_batch (created_at DESC);

COMMENT ON TABLE mukerrer_arsiv_batch IS 'A5: grup bazlı mükerrer arşiv onayları (geri alınabilir)';
COMMENT ON COLUMN mukerrer_arsiv_batch.archived_ids IS 'Arşivlenen customers.id listesi';
COMMENT ON COLUMN mukerrer_arsiv_batch.kanonik_id IS 'Kullanıcının seçtiği kanonik customers.id';
