"""Corpus assembly orchestrator.

Run from repo root:
    python -m scripts.build_vocab_corpus

Steps:
    1. Fresh-create assets/vocab_bundled.db
    2. Apply schema (DDL + sqlite-vec virtual table)
    3. For each source module under sources/:
        - call iter_terms()
        - dedupe globally (case-insensitive on canonical_form, by category)
        - compute Double Metaphone codes
        - bulk-insert into vocab_bundled
    4. Compute Nomic embeddings for each row
    5. Insert embeddings into vocab_bundled_vec
    6. Write metadata row (corpus_version, build_timestamp, per-category counts)
    7. ANALYZE / VACUUM for query-plan stability + size

Each source module is independent; failures in one don't block the
others. Build emits a JSON report at the end with per-source counts so
we can detect upstream-source regressions release over release.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .schema import (
    CATEGORIES,
    EMBEDDING_DIM,
    SCHEMA_SQL,
    SQLITE_VEC_DDL,
    deterministic_id,
)
from .types import RawTerm

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "assets" / "vocab_bundled.db"

# Source modules to invoke, in dependency order. Each is a Python module
# under sources/ exposing iter_terms() -> Iterable[RawTerm]. New sources
# are added here; missing/failing sources don't block the build, they
# just emit a warning + zero-count line in the build report.
SOURCE_MODULES = [
    # Curated lists (no network — fast + always-available)
    "latin_legal",
    "military_ranks",
    "military_acronyms",
    "business_acronyms",
    "colloquial_slang",
    "government_acronyms",
    "law_enforcement_codes",
    # Network-fetched, public-domain US gov + permissive
    "nasa_acronyms",
    "sec_edgar",
    "ourairports",
    "geonames_cities",
    "iso_639_languages",
    "dod_dictionary",
    "scowl",
    "cmudict_homophones",
    "wikidata_entities",
    "tech_terms",
    "medical_curated",
    "religious_cultural",
    "cooking_food",
    # Future modules (commented until source extractors land):
    # "scowl",
    # "cmudict",
    # "norvig_frequency",
    # "mesh",
    # "rxnorm",
    # "icd10cm",
    # "court_listener",
    # "wikidata_entities",
    # "stack_overflow_tags",
    # "awesome_lists",
    # "kaikki_slang",
    # "musicbrainz",
    # "wikidata_sports",
    # "doj_acronyms",
    # "wikipedia_ten_codes",
    # "wikidata_food",
    # "ipeds_universities",
    # "wikipedia_religious",
    # "pubchem_top",
    # "courtside_misrecognitions",
]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )


def _doublemetaphone(text: str) -> tuple[str, str]:
    """Compute Double Metaphone codes. Falls back to ('','') if package
    is missing — the runtime correction code already tolerates that."""
    try:
        from metaphone import doublemetaphone
        primary, alternate = doublemetaphone(text)
        return (primary or "", alternate or "")
    except Exception:
        return ("", "")


def _open_database(path: Path) -> sqlite3.Connection:
    """Create a fresh DB at *path*, load sqlite-vec, apply schema."""
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA_SQL)

    # sqlite-vec optional load. The runtime side has the same fallback;
    # building without sqlite-vec produces a usable DB minus the vector
    # table, which gracefully degrades to BM25-only retrieval at runtime.
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute(SQLITE_VEC_DDL)
        logger.info("sqlite-vec extension loaded; vector table created")
    except Exception as e:
        logger.warning(
            "sqlite-vec not available (%s) — corpus will ship without vector "
            "index, runtime falls back to BM25-only retrieval", e,
        )
    return conn


def _normalize_term(t: RawTerm) -> RawTerm | None:
    """Trim, validate, and dedup-key a RawTerm. Returns None to skip."""
    term = (t.term or "").strip()
    canonical = (t.canonical_form or term).strip()
    if not term or not canonical or len(canonical) < 2:
        return None
    if t.category not in CATEGORIES:
        logger.warning("unknown category %r on term %r — skipping", t.category, canonical)
        return None
    return RawTerm(
        term=term,
        canonical_form=canonical,
        category=t.category,
        subcategory=t.subcategory,
        sounds_like=[s for s in (t.sounds_like or []) if s.strip()],
        context_blurb=(t.context_blurb or "").strip()[:200],
        popularity_score=max(0.0, min(1.0, t.popularity_score)),
        source=t.source,
    )


def _bulk_insert(conn: sqlite3.Connection, terms: Iterable[RawTerm]) -> dict[str, int]:
    """Insert terms into vocab_bundled, deduping by deterministic ID.

    Returns per-category insertion counts for the build report.
    """
    counts: dict[str, int] = {}
    seen_ids: set[int] = set()
    rows: list[tuple] = []

    for raw in terms:
        norm = _normalize_term(raw)
        if norm is None:
            continue
        row_id = deterministic_id(norm.canonical_form, norm.category)
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        primary, secondary = _doublemetaphone(norm.canonical_form)
        sounds_like_str = ",".join(norm.sounds_like) if norm.sounds_like else None
        rows.append((
            row_id,
            norm.term,
            norm.canonical_form,
            norm.category,
            norm.subcategory,
            sounds_like_str,
            primary,
            secondary,
            norm.context_blurb,
            norm.popularity_score,
            norm.source,
        ))
        counts[norm.category] = counts.get(norm.category, 0) + 1

        if len(rows) >= 1000:
            conn.executemany(
                "INSERT OR IGNORE INTO vocab_bundled "
                "(id, term, canonical_form, category, subcategory, sounds_like, "
                " metaphone_primary, metaphone_secondary, context_blurb, "
                " popularity_score, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            rows.clear()

    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO vocab_bundled "
            "(id, term, canonical_form, category, subcategory, sounds_like, "
            " metaphone_primary, metaphone_secondary, context_blurb, "
            " popularity_score, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    conn.commit()
    return counts


def _embed_corpus(conn: sqlite3.Connection) -> int:
    """Compute and insert Nomic embeddings for all rows.

    Skipped (with a warning) when the embedder isn't available — we
    can't download the model in every CI environment, so the build
    falls through to vector-less mode in that case.

    Returns count of embedded rows.
    """
    try:
        # Reuse the same embedder the runtime uses for semantic search
        # so embeddings are perfectly comparable at query time.
        sys.path.insert(0, str(REPO_ROOT / "packages" / "backend"))
        from services.embedding import get_embedder
        embedder = get_embedder()
    except Exception as e:
        logger.warning("Embedder unavailable (%s) — building DB without vectors", e)
        return 0

    cur = conn.execute(
        "SELECT id, canonical_form, context_blurb FROM vocab_bundled"
    )
    rows = cur.fetchall()
    if not rows:
        return 0

    BATCH = 256
    embedded = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        # Embed the canonical form plus a short context blurb (the
        # context disambiguates ambiguous strings like "GO" the
        # programming language vs. "go" the verb).
        texts = [
            (r["canonical_form"] + " " + (r["context_blurb"] or "")).strip()
            for r in chunk
        ]
        try:
            vectors = embedder.embed(texts)
        except Exception as exc:
            logger.warning("Embedder batch failed at offset %d: %s — skipping", i, exc)
            continue

        # Insert into sqlite-vec. If the vec table wasn't created
        # (sqlite-vec missing) this raises and we skip silently.
        try:
            data = [
                (r["id"], v.astype("float32").tobytes())
                for r, v in zip(chunk, vectors)
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO vocab_bundled_vec (term_id, embedding) VALUES (?, ?)",
                data,
            )
            embedded += len(chunk)
        except Exception as exc:
            logger.warning("vec insert failed at offset %d: %s — embeddings off", i, exc)
            return 0

        if (i // BATCH) % 10 == 0:
            logger.info("Embedded %d / %d", i + len(chunk), len(rows))

    conn.commit()
    return embedded


def _write_metadata(
    conn: sqlite3.Connection,
    *,
    corpus_version: str,
    counts: dict[str, int],
    embedded_count: int,
    duration_s: float,
) -> None:
    meta = {
        "corpus_version": corpus_version,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "build_duration_seconds": round(duration_s, 2),
        "total_terms": sum(counts.values()),
        "embedded_count": embedded_count,
        "category_counts": counts,
        "schema_version": "1",
        "embedding_dim": EMBEDDING_DIM,
    }
    conn.executemany(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        [(k, json.dumps(v) if not isinstance(v, str) else v) for k, v in meta.items()],
    )
    conn.commit()


def _write_csv_export(
    conn: sqlite3.Connection,
    *,
    output_dir: Path,
    counts: dict[str, int],
) -> Path:
    """Dump the corpus to a master CSV + per-category CSVs.

    The master is sorted by (category, term) so a `grep` or `sort` against
    it is well-behaved. Per-category files keep row counts low enough to
    open cleanly in Excel/Numbers (Excel chokes around the 1M-row
    boundary, our largest category will be well under 200k).

    The CSVs ship alongside the DB but are not used at runtime — they're
    a personal-QA / editorial-review artifact.
    """
    import csv

    output_dir.mkdir(parents=True, exist_ok=True)
    master_path = output_dir / "vocab_corpus.csv"

    columns = [
        "category",
        "subcategory",
        "term",
        "canonical_form",
        "sounds_like",
        "popularity_score",
        "metaphone_primary",
        "metaphone_secondary",
        "context_blurb",
        "source",
    ]

    cur = conn.execute(
        f"SELECT {', '.join(columns)} FROM vocab_bundled "
        f"ORDER BY category, term COLLATE NOCASE"
    )

    # Master CSV
    with master_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(columns)
        for row in cur:
            writer.writerow([(v if v is not None else "") for v in row])

    # Per-category CSVs in a subdir for easy spreadsheet opening
    by_category_dir = output_dir / "by_category"
    by_category_dir.mkdir(exist_ok=True)
    for category in counts:
        path = by_category_dir / f"{category}.csv"
        cur = conn.execute(
            f"SELECT {', '.join(columns)} FROM vocab_bundled "
            f"WHERE category = ? ORDER BY term COLLATE NOCASE",
            (category,),
        )
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(columns)
            for row in cur:
                writer.writerow([(v if v is not None else "") for v in row])

    # README so the user knows what they're looking at
    readme = output_dir / "README.md"
    readme.write_text(f"""# Verbatim Bundled Vocabulary Corpus — QA Export

This directory contains the bundled-vocabulary corpus in human-readable
form, generated alongside `vocab_bundled.db`. The CSVs are reference
artifacts for editorial review only — the runtime app reads from the
SQLite database, not these files.

## Files

- `vocab_corpus.csv` — single sorted CSV of all {sum(counts.values()):,} terms,
  ordered by (category, term). Useful for `grep`, `sort`, `awk`, or
  loading into a database / dataframe for ad-hoc queries.
- `by_category/<category>.csv` — one CSV per category, sorted by term.
  Smaller files open cleanly in Excel / Numbers / LibreOffice Calc.

## Columns

- `category` — top-level bucket (general, tech, medical, legal, etc.)
- `subcategory` — finer grouping where the source provides one
- `term` — the lookup form (lowercased / ASCII-folded for matching)
- `canonical_form` — the spelling we want in transcripts
- `sounds_like` — comma-separated phonetic alternates (Speechmatics-style)
- `popularity_score` — 0.0-1.0 editorial weight (1.0 = extremely common)
- `metaphone_primary` / `metaphone_secondary` — Double Metaphone codes
  used by the runtime phonetic-correction matcher
- `context_blurb` — short example phrase, embedded for semantic retrieval
- `source` — attribution string (matches THIRD_PARTY_LICENSES.md)

## Per-category counts

{chr(10).join(f"- **{cat}**: {n:,} terms" for cat, n in sorted(counts.items()))}

**Total: {sum(counts.values()):,} terms**

## Reviewing

To spot bad entries:

```bash
# All terms shorter than 3 chars (often false positives)
awk -F, 'length($3) < 3' vocab_corpus.csv | head

# Terms in a specific category
awk -F, '$1 == "military"' vocab_corpus.csv | head

# Terms whose canonical form is all lowercase (likely not proper nouns)
awk -F, '$4 ~ /^[a-z]+$/' vocab_corpus.csv | head

# Find dups across categories
cut -d, -f3 vocab_corpus.csv | sort | uniq -c | sort -rn | head
```
""")

    return master_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--corpus-version", default="0.1.0")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--skip-embeddings", action="store_true",
        help="Don't compute Nomic embeddings (faster builds for testing).",
    )
    parser.add_argument(
        "--sources", nargs="*",
        help="Override the list of source modules. Defaults to SOURCE_MODULES.",
    )
    parser.add_argument(
        "--csv-export-dir", type=Path,
        default=REPO_ROOT / "assets" / "vocab_corpus_export",
        help="Directory for the human-readable CSV export. Defaults to "
             "assets/vocab_corpus_export/. Pass empty string to skip.",
    )
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    started = time.monotonic()
    logger.info("Building vocab corpus → %s", args.output)
    conn = _open_database(args.output)

    sources = args.sources or SOURCE_MODULES
    counts: dict[str, int] = {}
    per_source_counts: dict[str, int] = {}

    for src in sources:
        full = f"scripts.build_vocab_corpus.sources.{src}"
        try:
            module = importlib.import_module(full)
        except Exception as e:
            logger.error("Source %s failed to import: %s", src, e)
            per_source_counts[src] = 0
            continue

        logger.info("Source: %s", getattr(module, "name", src))
        try:
            terms = list(module.iter_terms())
        except Exception as e:
            logger.error("Source %s iter_terms() raised: %s", src, e)
            per_source_counts[src] = 0
            continue

        before = sum(counts.values())
        delta = _bulk_insert(conn, terms)
        for cat, n in delta.items():
            counts[cat] = counts.get(cat, 0) + n
        per_source_counts[src] = sum(counts.values()) - before
        logger.info("  → %d terms inserted", per_source_counts[src])

    embedded = 0 if args.skip_embeddings else _embed_corpus(conn)

    duration = time.monotonic() - started
    _write_metadata(
        conn,
        corpus_version=args.corpus_version,
        counts=counts,
        embedded_count=embedded,
        duration_s=duration,
    )

    # Human-readable CSV export for QA review. Done before VACUUM since
    # we still hold the analyze-friendly connection. Empty --csv-export-dir
    # turns it off (use case: CI builds where we only care about the .db).
    csv_master_path: Path | None = None
    if args.csv_export_dir and str(args.csv_export_dir):
        csv_master_path = _write_csv_export(
            conn, output_dir=args.csv_export_dir, counts=counts,
        )
        logger.info("CSV QA export written to %s", csv_master_path)

    logger.info("ANALYZE / VACUUM …")
    conn.execute("ANALYZE")
    conn.commit()
    conn.close()

    # VACUUM in a separate connection because it can't run inside a transaction.
    conn = sqlite3.connect(args.output)
    conn.execute("VACUUM")
    conn.close()

    size_mb = args.output.stat().st_size / 1024 / 1024
    logger.info(
        "Built corpus v%s with %d terms (%d embedded) in %.1fs → %.1f MB",
        args.corpus_version, sum(counts.values()), embedded, duration, size_mb,
    )
    print(json.dumps({
        "corpus_version": args.corpus_version,
        "duration_s": round(duration, 1),
        "size_mb": round(size_mb, 1),
        "category_counts": counts,
        "per_source_counts": per_source_counts,
        "embedded": embedded,
        "csv_export": str(csv_master_path) if csv_master_path else None,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
