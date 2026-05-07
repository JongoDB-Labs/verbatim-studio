"""Add Nomic embeddings to an already-built vocab_bundled.db.

The full corpus pipeline (`python -m scripts.build_vocab_corpus`) wipes
and rebuilds from sources, which is expensive and hits the network. When
sources haven't changed but you want to upgrade a BM25-only DB to the
full hybrid mode (BM25 + cosine), this script just runs the embedding
pass over the existing rows.

Usage:
    python -m scripts.build_vocab_corpus.embed_existing
    python -m scripts.build_vocab_corpus.embed_existing --output assets/vocab_bundled.db

Idempotent: re-running over a DB that already has embeddings replaces
them (INSERT OR REPLACE). Skips the source-fetch + rebuild pass entirely.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import struct
import sys
import time
from pathlib import Path

from .schema import EMBEDDING_DIM, SQLITE_VEC_DDL

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "assets" / "vocab_bundled.db"


def _open_with_vec(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"DB not found: {path} — run `python -m scripts.build_vocab_corpus` first")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception as e:
        raise SystemExit(
            f"sqlite-vec extension required but unavailable: {e}\n"
            "Install with: pip install sqlite-vec"
        )
    # Ensure the vec virtual table exists. Idempotent — CREATE IF NOT EXISTS.
    conn.execute(SQLITE_VEC_DDL)
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--batch-size", type=int, default=256,
        help="Embedder batch size (lower if memory-constrained).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    started = time.monotonic()
    conn = _open_with_vec(args.output)

    from .__main__ import _get_build_embedder
    embedder = _get_build_embedder()

    cur = conn.execute(
        "SELECT id, canonical_form, context_blurb FROM vocab_bundled ORDER BY id"
    )
    rows = cur.fetchall()
    total = len(rows)
    if total == 0:
        logger.warning("vocab_bundled has no rows — nothing to embed")
        return 0

    logger.info("Embedding %d terms (batch=%d, dim=%d)…", total, args.batch_size, EMBEDDING_DIM)

    embedded = 0
    for i in range(0, total, args.batch_size):
        chunk = rows[i:i + args.batch_size]
        texts = [
            (r["canonical_form"] + " " + (r["context_blurb"] or "")).strip()
            for r in chunk
        ]
        try:
            vectors = embedder.embed_documents_sync(texts)
        except Exception as exc:
            logger.warning("batch failed at offset %d: %s — skipping", i, exc)
            continue

        data = [
            (r["id"], struct.pack(f"<{len(v)}f", *v))
            for r, v in zip(chunk, vectors)
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO vocab_bundled_vec (term_id, embedding) VALUES (?, ?)",
            data,
        )
        conn.commit()
        embedded += len(chunk)

        elapsed = time.monotonic() - started
        rate = embedded / elapsed if elapsed > 0 else 0
        eta_s = (total - embedded) / rate if rate > 0 else 0
        if (i // args.batch_size) % 5 == 0:
            logger.info(
                "  %d/%d (%.1f%%, %.1f terms/s, ETA %.1f min)",
                embedded, total, embedded / total * 100, rate, eta_s / 60,
            )

    duration = time.monotonic() - started
    logger.info("Done — embedded %d/%d terms in %.1f min", embedded, total, duration / 60)

    # Update metadata so the runtime can confirm the corpus has vectors.
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('embedded_count', ?)",
        (str(embedded),),
    )
    conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
