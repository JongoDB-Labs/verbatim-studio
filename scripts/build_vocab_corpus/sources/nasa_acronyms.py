"""NASA Acronyms source.

Bundles the NASA-Acronyms dataset. Despite the NASA branding, the
content is mostly generic government/technical acronyms (TBD, FY, ROI,
GUI, CLI) plus a NASA-specific tail. We strip clearly NASA-internal
entries (mission codes, internal program names) and keep the
cross-domain ones.

Source: https://github.com/nasa/NASA-Acronyms (acronyms.json)
License: MIT
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

from ..pronunciation import derive_acronym_pronunciations
from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "vocab_corpus_cache" / "nasa_acronyms"
SOURCE_URL = "https://raw.githubusercontent.com/nasa/NASA-Acronyms/master/acronyms.json"

# Heuristic: drop entries whose definition contains NASA-internal markers.
# Keeps the dataset useful for non-NASA users while preserving the
# generic government / tech / aerospace acronyms.
NASA_INTERNAL_RE = re.compile(
    r"\b(NASA|JPL|Goddard|Marshall|Langley|Kennedy|Johnson|Glenn|Stennis|Ames|Wallops)\b",
    re.IGNORECASE,
)

# Acronyms that are very common English words ("AS", "BY", "TO") cause
# false-positive Whisper substitutions. Drop those — the standard-English
# word check in vocab_correction would catch them anyway, but pruning at
# build time saves embedding cost.
_STOPWORDS = {
    "as", "by", "to", "is", "it", "in", "on", "of", "or", "if", "an", "at",
    "be", "do", "go", "up", "us", "we", "no", "so", "my", "me", "the",
    "and", "but", "for", "are", "was", "you", "all", "any", "one", "two",
    "use", "yes",
}


def _ensure_dataset() -> Path:
    """Download acronyms.json into the cache if missing.

    Build script is expected to be run with internet access in CI.
    Cached file is reused on subsequent invocations so reproducible
    builds don't re-download.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / "acronyms.json"
    if target.exists():
        return target

    import urllib.request
    logger.info("Fetching NASA acronyms from %s", SOURCE_URL)
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as resp:
        target.write_bytes(resp.read())
    return target


def iter_terms() -> Iterable[RawTerm]:
    """Yield generic acronyms from the NASA Acronyms JSON.

    Schema (per source repo): list of {abbreviation, expansion, source, ...}.
    `(EUS)`-prefixed entries are program-specific noise — strip them.
    """
    path = _ensure_dataset()
    data = json.loads(path.read_text(encoding="utf-8"))

    seen: set[str] = set()
    yielded = 0
    skipped_internal = 0
    skipped_stopword = 0
    skipped_noise = 0

    for row in data:
        acronym = (row.get("abbreviation") or "").strip()
        definition = (row.get("expansion") or "").strip()

        # Strip leading "(EUS)" / "(V)" / "(E)" prefixes which are
        # program-scope qualifiers, not actual acronym variants.
        clean = re.sub(r"^\([A-Z]+\)", "", acronym).strip()
        if clean != acronym:
            acronym = clean
        if not acronym:
            skipped_noise += 1
            continue
        # Skip multi-acronym entries with parens or slashes (rare).
        if any(c in acronym for c in "()/"):
            skipped_noise += 1
            continue

        if not acronym or len(acronym) < 2:
            continue
        if acronym.lower() in _STOPWORDS:
            skipped_stopword += 1
            continue
        if NASA_INTERNAL_RE.search(definition):
            skipped_internal += 1
            continue

        # Dedup on the acronym alone — the same acronym with multiple
        # definitions is common (FY = "Fiscal Year" and "Fiscal Year").
        # Keep the first definition encountered for the context_blurb.
        key = acronym.upper()
        if key in seen:
            continue
        seen.add(key)

        # Acronyms at this point are nearly always upper-cased in source;
        # store them upper-cased as canonical so transcripts get the
        # familiar form. The lookup-side `term` is also upper to match
        # how Whisper would produce them when the prompt is biased.
        canonical = acronym.upper()
        # popularity_score: short acronyms (2-3 chars) tend to be the
        # high-frequency ones (FY, ROI, GUI). Longer acronyms (PRC-117G)
        # are domain-specific. Use length as a rough proxy: 2-3 chars
        # → 0.6, 4-5 chars → 0.4, 6+ → 0.2.
        score = 0.6 if len(canonical) <= 3 else 0.4 if len(canonical) <= 5 else 0.2

        # Auto-derived letter-by-letter pronunciation. NASA's CSV doesn't
        # tell us which acronyms are spoken as words (NASA, JPL) vs.
        # spelled out (FY, ROI), so we ship the safe letter-form only.
        # Curated sources (military_acronyms, business_acronyms) layer
        # explicit word-form hints on top.
        sounds_like = derive_acronym_pronunciations(canonical)

        yield RawTerm(
            term=canonical,
            canonical_form=canonical,
            category="business",  # most NASA acronyms are gov/biz/tech crossover
            subcategory="acronym",
            sounds_like=sounds_like,
            context_blurb=definition[:120] if definition else "",
            popularity_score=score,
            source="NASA-Acronyms (MIT)",
        )
        yielded += 1

    logger.info(
        "NASA acronyms: %d yielded, %d skipped (internal), %d skipped (stopword)",
        yielded, skipped_internal, skipped_stopword,
    )


name = "NASA Acronyms"
category = "business"
