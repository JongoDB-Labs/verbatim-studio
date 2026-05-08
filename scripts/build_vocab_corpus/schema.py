"""Schema + DDL for the bundled vocabulary database.

Single SQLite file (`vocab_bundled.db`) ships with the app. At runtime,
the app reads from this DB and writes user additions to a separate file
(`vocab_user.db`) so app updates can replace the bundled side without
touching user data.

# Tables

`vocab_bundled` — read-only term table populated at build time.
`vocab_bundled_fts` — FTS5 index over (term, canonical_form, context_blurb).
`vocab_bundled_vec` — sqlite-vec virtual table holding 768-dim Nomic
                      embeddings for each term row.
`metadata` — single-row table with corpus version, build timestamp, and
             per-category counts. Used for migration logic at runtime.

# ID strategy

Bundled IDs are deterministic: hash(canonical_form + ":" + category) so
the same term has the same ID across corpus rebuilds. This lets user-
side `bundled_dedupe_id` references stay valid when the bundled DB is
refreshed in a future app release.
"""

from __future__ import annotations

import hashlib

# 768-dim embeddings from nomic-embed-text-v1.5. Stored as raw float32
# in sqlite-vec virtual tables; 8-bit quantization can be applied later
# if disk pressure increases.
EMBEDDING_DIM = 768

CATEGORIES = (
    # Original six (still bundled, just not user-visible).
    "general",
    "tech",
    "medical",
    "legal",
    "proper_nouns",
    "business",
    # Expanded set per 2026-05-07 plan.
    "military",
    "slang",
    "entertainment",
    "sports",
    "government",
    "aviation",
    "law_enforcement",
    "cooking",
    "education",
    "religious",
    "science",
    "real_estate",
    "languages",
    "math",
    # Special category for known-bad → known-good seed pairs collected
    # from research papers (Whisper Courtside) + CMUdict homophones.
    "misrecognition_seeds",
)


# Schema DDL — applied to a fresh DB at build start.
# vocab_bundled stores the canonical term; sounds_like alternates are
# comma-separated text (parsed at runtime). metaphone codes precomputed
# at build so the runtime phonetic-match path doesn't pay that cost
# per-lookup.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vocab_bundled (
    id                  INTEGER PRIMARY KEY,
    term                TEXT NOT NULL,
    canonical_form      TEXT NOT NULL,
    category            TEXT NOT NULL,
    subcategory         TEXT,
    sounds_like         TEXT,
    metaphone_primary   TEXT,
    metaphone_secondary TEXT,
    context_blurb       TEXT,
    popularity_score    REAL DEFAULT 0,
    source              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vocab_bundled_term
    ON vocab_bundled(term COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_vocab_bundled_metaphone_p
    ON vocab_bundled(metaphone_primary);
CREATE INDEX IF NOT EXISTS idx_vocab_bundled_metaphone_s
    ON vocab_bundled(metaphone_secondary);
CREATE INDEX IF NOT EXISTS idx_vocab_bundled_category
    ON vocab_bundled(category);

-- FTS5 index for keyword retrieval. Synced via insert/update/delete
-- triggers below. content='vocab_bundled' makes it an external-content
-- index — saves disk by referencing the row in the parent table.
CREATE VIRTUAL TABLE IF NOT EXISTS vocab_bundled_fts USING fts5(
    term,
    canonical_form,
    context_blurb,
    content='vocab_bundled',
    content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 1'
);

-- Triggers keep the FTS index in sync with the source table during
-- the build. (At runtime the table is read-only so triggers don't fire.)
CREATE TRIGGER IF NOT EXISTS vocab_bundled_fts_insert
AFTER INSERT ON vocab_bundled BEGIN
    INSERT INTO vocab_bundled_fts(rowid, term, canonical_form, context_blurb)
    VALUES (new.id, new.term, new.canonical_form, new.context_blurb);
END;

CREATE TRIGGER IF NOT EXISTS vocab_bundled_fts_delete
AFTER DELETE ON vocab_bundled BEGIN
    INSERT INTO vocab_bundled_fts(vocab_bundled_fts, rowid, term, canonical_form, context_blurb)
    VALUES ('delete', old.id, old.term, old.canonical_form, old.context_blurb);
END;

CREATE TRIGGER IF NOT EXISTS vocab_bundled_fts_update
AFTER UPDATE ON vocab_bundled BEGIN
    INSERT INTO vocab_bundled_fts(vocab_bundled_fts, rowid, term, canonical_form, context_blurb)
    VALUES ('delete', old.id, old.term, old.canonical_form, old.context_blurb);
    INSERT INTO vocab_bundled_fts(rowid, term, canonical_form, context_blurb)
    VALUES (new.id, new.term, new.canonical_form, new.context_blurb);
END;
"""

# sqlite-vec virtual-table DDL. The extension must be loaded before
# the table can be created.
#
# We store embeddings as INT8 (1 byte/dim) instead of FLOAT (4 bytes/dim).
# At ~555K terms × 768 dims, this is the difference between a 1.7 GB
# corpus and a ~430 MB corpus. Per-dim calibration scales are stored
# in the metadata table (key="vec_int8_scales") so the runtime can
# apply the same quantization to the query vector.
#
# Accuracy impact on Nomic-embed-v1.5 (L2-normalized): ~0.5% retrieval
# recall loss at top-200, which falls on the cosine-leg ranking — the
# safety pool, BM25 leg, and Phase 2 phonetic correction are all
# unaffected.
SQLITE_VEC_DDL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vocab_bundled_vec USING vec0(
    term_id INTEGER PRIMARY KEY,
    embedding INT8[{EMBEDDING_DIM}]
);
"""


def deterministic_id(canonical_form: str, category: str) -> int:
    """Stable, reproducible 63-bit ID derived from (canonical_form, category).

    Hashing means corpus rebuilds produce the same row IDs, which keeps
    `bundled_dedupe_id` references in the user table valid across app
    releases. SQLite INTEGER PK is a signed 64-bit, so we mask to 63 bits
    to keep the value positive.
    """
    key = f"{canonical_form.strip().lower()}::{category}"
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    raw = int.from_bytes(digest, "big", signed=False)
    return raw & ((1 << 63) - 1)
