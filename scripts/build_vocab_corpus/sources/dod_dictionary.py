"""DOD Dictionary of Military and Associated Terms (JP 1-02 successor).

US federal work — public domain. The DoD Terminology Program publishes
this PDF; the FAS mirror provides a stable URL. We extract acronyms +
short terms; long doctrinal definitions are dropped.

Source: https://irp.fas.org/doddir/dod/dictionary.pdf
License: Public domain (US federal work)

Note: PDF parsing is fragile. The build script's CI environment has
PyMuPDF available (already a backend dep). When parsing fails, the
source emits zero terms — the build report flags it but doesn't abort.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "vocab_corpus_cache" / "dod_dictionary"
SOURCE_URL = "https://irp.fas.org/doddir/dod/dictionary.pdf"

# Acronym pattern: 2-15 chars, uppercase letters with optional numbers,
# slashes, dashes, ampersands. Matches MCTSSA, F-35, AN/PRC-117G, etc.
_ACRONYM_RE = re.compile(r"\b([A-Z][A-Z0-9&/\-]{1,14})\b")

# Acronym definition pattern: "ACRONYM —" or "ACRONYM. " followed by expansion.
_ACRONYM_DEF_RE = re.compile(
    r"^([A-Z][A-Z0-9&/\-]{1,14})\s+[—\-–\.]\s+([A-Z][^\.]{5,150}\.)",
    re.MULTILINE,
)

# Common-English false positives that match the acronym regex but are
# normal words (would bias Whisper toward random capitalization).
_NOISE = {
    "THE", "AND", "FOR", "WITH", "ARE", "WAS", "HAS", "HAVE", "BEEN",
    "WILL", "THIS", "THAT", "THESE", "THOSE", "FROM", "WHEN", "WHERE",
    "BEFORE", "AFTER", "DURING", "BETWEEN",
}


def _ensure_dataset() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / "dod_dictionary.pdf"
    if target.exists():
        return target

    import urllib.request
    logger.info("Fetching DoD dictionary from %s", SOURCE_URL)
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "VerbatimStudio-CorpusBuild/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        target.write_bytes(resp.read())
    return target


def iter_terms() -> Iterable[RawTerm]:
    """Extract acronyms + their expansions from the DoD dictionary PDF."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF not available — DoD source emits 0 terms")
        return

    try:
        path = _ensure_dataset()
    except Exception as e:
        logger.warning("DoD dictionary fetch failed: %s — emitting 0 terms", e)
        return

    try:
        doc = fitz.open(path)
    except Exception as e:
        logger.warning("PDF open failed: %s", e)
        return

    full_text = ""
    for page in doc:
        full_text += page.get_text("text") + "\n"
    doc.close()

    seen: set[str] = set()
    yielded = 0

    # First pass: structured acronym—definition lines.
    for match in _ACRONYM_DEF_RE.finditer(full_text):
        acronym = match.group(1).strip()
        definition = match.group(2).strip().rstrip(".")
        if acronym in _NOISE or len(acronym) < 2:
            continue
        if acronym in seen:
            continue
        seen.add(acronym)
        yielded += 1

        # Acronym length proxy for popularity.
        score = 0.7 if len(acronym) <= 4 else 0.5

        yield RawTerm(
            term=acronym,
            canonical_form=acronym,
            category="military",
            subcategory="acronym",
            context_blurb=definition[:140],
            popularity_score=score,
            source="DoD Dictionary of Military and Associated Terms (public domain)",
        )

    logger.info("DoD dictionary: %d acronyms extracted", yielded)


name = "DoD Dictionary"
category = "military"
