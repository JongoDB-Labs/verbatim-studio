"""Bundled-vocabulary corpus assembly tooling.

Run `python -m scripts.build_vocab_corpus` from the repo root to produce
`assets/vocab_bundled.db` — the corpus that ships with Verbatim Studio.

Each category lives in its own submodule with a `fetch()` and `extract()`
contract so sources can be refreshed independently. The orchestrator in
`__main__.py` coordinates the per-category outputs into a single SQLite
database with FTS5 + sqlite-vec indexes.

Architecture decisions documented in:
docs/plans/2026-05-07-bundled-vocabulary-redesign.md
"""

from __future__ import annotations

__version__ = "0.1.0"
