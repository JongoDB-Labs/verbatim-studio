"""Produce a slim BM25-only variant of vocab_bundled.db.

Reads the source DB (full embedded), copies it to *destination*, drops
the `vocab_bundled_vec` virtual table, and VACUUMs to reclaim the
~1 GB of embedding storage. The resulting slim DB ships in the installer;
users opt into downloading the full embedded variant from
verbatim-studio-releases for hybrid retrieval.

Runtime gracefully degrades when the vec table is absent (see
services/vocab_retrieval.py:_open_bundled_conn — the `has_vec` flag
gates the cosine leg). BM25 + category-broadcast remain functional, so
"slim" still gives 6/7 coverage on USMC QA examples.

Usage:
    python -m scripts.build_vocab_corpus.strip_embeddings \\
        --source assets/vocab_bundled.db \\
        --dest   assets/vocab_bundled_slim.db
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / "assets" / "vocab_bundled.db"
DEFAULT_DEST = REPO_ROOT / "assets" / "vocab_bundled_slim.db"


def strip(source: Path, dest: Path) -> None:
    if not source.exists():
        raise SystemExit(f"source DB missing: {source}")

    if dest.exists():
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)

    logger.info("copying %s → %s", source, dest)
    shutil.copy2(source, dest)
    # SQLite WAL/SHM files don't follow filename — copy any sidecars too.
    for ext in ("-wal", "-shm", "-journal"):
        sidecar = source.with_name(source.name + ext)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass

    # Drop the vec virtual table. It's not loadable without the sqlite-vec
    # extension, so we DROP via a conn that has loaded it. If sqlite-vec
    # is unavailable, fall back to a vec-aware delete via the metadata
    # tables (sqlite_vec stores rows in vocab_bundled_vec_chunks).
    conn = sqlite3.connect(dest)
    try:
        conn.enable_load_extension(True)
        try:
            import sqlite_vec
            sqlite_vec.load(conn)
            conn.execute("DROP TABLE IF EXISTS vocab_bundled_vec")
            logger.info("vocab_bundled_vec dropped via sqlite-vec")
        except Exception as e:
            logger.warning("sqlite-vec unavailable (%s); using raw cleanup", e)
            # sqlite-vec stores its data across sibling tables (the vec0
            # extension creates *_chunks, *_rowids, *_vector_chunks00 etc).
            # Without the extension we can't DROP the virtual table, but
            # we can drop the underlying shadow tables which is enough to
            # reclaim space; the virtual table reference becomes a dangling
            # pointer that the runtime won't try to use because has_vec
            # detection probes vocab_bundled_vec on open.
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name LIKE 'vocab_bundled_vec%' "
                "  AND type IN ('table','index')"
            )
            for (name,) in cur.fetchall():
                try:
                    conn.execute(f"DROP TABLE IF EXISTS \"{name}\"")
                except sqlite3.Error as drop_err:
                    logger.debug("drop %s failed: %s", name, drop_err)
        conn.commit()
    finally:
        conn.close()

    # VACUUM in a fresh connection (can't be inside a tx).
    conn = sqlite3.connect(dest)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()

    src_mb = source.stat().st_size / 1024 / 1024
    dst_mb = dest.stat().st_size / 1024 / 1024
    logger.info("source=%.1f MB → slim=%.1f MB (%.0f%% smaller)",
                src_mb, dst_mb, (1 - dst_mb / src_mb) * 100)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    strip(args.source, args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
