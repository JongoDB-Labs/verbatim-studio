"""Shared dataclasses + protocols for the corpus builder.

Each per-category source module produces a stream of `RawTerm` records,
which the orchestrator dedupes, embeds, and inserts. Sources never
write to the DB directly — they emit terms and the orchestrator
handles persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol


@dataclass
class RawTerm:
    """A single term produced by a source.

    `term` is the lookup form (lowercased, ASCII-folded for matching).
    `canonical_form` is what we want to see in transcripts (e.g., "MCTSSA"
    not "mctssa", "MacBook" not "macbook"). When a source emits both
    forms the same, both fields hold the same value.

    `popularity_score` is an editorial weight roughly normalised to
    [0, 1]: 1.0 = "extremely common" (Apple, Google, Q4), 0.0 = "obscure"
    (rare drug name). Sources compute this themselves; the orchestrator
    uses it as one of the retrieval ranking signals.
    """

    term: str
    canonical_form: str
    category: str
    subcategory: str | None = None
    sounds_like: list[str] = field(default_factory=list)
    context_blurb: str = ""  # short example phrase for embedding
    popularity_score: float = 0.0
    source: str = ""  # attribution string


class TermSource(Protocol):
    """Per-category source module contract.

    Each source lives in `scripts/build_vocab_corpus/sources/<name>.py`
    and exposes a single `iter_terms()` function returning RawTerm
    instances. Sources are responsible for:
      - Downloading raw data (cached under `assets/vocab_corpus_cache/`)
      - Parsing into the RawTerm shape
      - Per-source dedup (no need to dedup against other sources)
      - Setting `category`, `source`, and `popularity_score`

    Cross-source dedup happens centrally in the orchestrator.
    """

    name: str  # display name, e.g. "MeSH"
    category: str

    def iter_terms(self) -> Iterable[RawTerm]: ...
