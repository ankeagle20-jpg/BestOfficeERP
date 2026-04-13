-- Akbank Excel manuel müşteri seçimi hatırlama (sender_key → musteri_id)
CREATE TABLE IF NOT EXISTS akbank_dekont_musteri_map (
    sender_key TEXT PRIMARY KEY,
    musteri_id INTEGER NOT NULL,
    ornek_aciklama TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_akbank_dekont_map_musteri ON akbank_dekont_musteri_map (musteri_id);
