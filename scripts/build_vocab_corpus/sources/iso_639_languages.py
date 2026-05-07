"""ISO 639 language codes and language names.

SIL maintains the canonical ISO 639-3 tables (~8,000 languages plus
codes and macrolanguage groupings). Whisper systematically misspells
less-common language names ("Tagalog" → "tag a long", "Quechua" →
"katchwa", etc.). Bundling the full ISO 639-3 reference fixes recognition
across language-tagged transcripts.

Source: https://iso639-3.sil.org/code_tables/639/data
License: Free use per SIL terms (no attribution-required clauses for
the table itself; the annotations have a BY-like clause but the codes
and names are factual/public).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "vocab_corpus_cache" / "iso_639"

# SIL distributes the table as a tab-separated download. The URL
# changes with the year; resolve through the index page if the direct
# link breaks.
SOURCE_URL = "https://iso639-3.sil.org/sites/iso639-3/files/downloads/iso-639-3.tab"


def _ensure_dataset() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / "iso-639-3.tab"
    if target.exists():
        return target

    import urllib.request
    logger.info("Fetching ISO 639-3 from %s", SOURCE_URL)
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "VerbatimStudio-CorpusBuild/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        target.write_bytes(resp.read())
    return target


def iter_terms() -> Iterable[RawTerm]:
    """Yield each language's preferred English name."""
    try:
        path = _ensure_dataset()
    except Exception as e:
        logger.warning("ISO 639 fetch failed: %s — emitting 0 terms", e)
        return

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return

    # Header: Id  Part2B  Part2T  Part1  Scope  Language_Type  Ref_Name  Comment
    header = lines[0].split("\t")
    try:
        ref_name_idx = header.index("Ref_Name")
        scope_idx = header.index("Scope")
    except ValueError:
        logger.warning("ISO 639-3 header layout unexpected: %s", header)
        return

    yielded = 0
    seen: set[str] = set()

    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) <= ref_name_idx:
            continue
        name = cols[ref_name_idx].strip()
        scope = cols[scope_idx].strip() if scope_idx < len(cols) else ""
        if not name or len(name) < 2:
            continue
        # Drop "scope=Special" entries (e.g., "Reserved for local use")
        if scope == "S":
            continue

        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        # Common languages get higher popularity.
        score = 0.9 if name.lower() in {
            "english", "spanish", "french", "german", "japanese", "korean",
            "mandarin chinese", "arabic", "portuguese", "russian", "italian",
            "hindi", "vietnamese", "thai", "turkish", "polish", "dutch",
            "swedish", "norwegian", "danish", "finnish", "greek", "hebrew",
        } else 0.3

        yield RawTerm(
            term=name,
            canonical_form=name,
            category="languages",
            subcategory="iso_639_3",
            context_blurb=f"{name} language",
            popularity_score=score,
            source="ISO 639-3 / SIL",
        )
        yielded += 1

    logger.info("ISO 639 languages: %d yielded", yielded)


name = "ISO 639 Languages"
category = "languages"
