"""MeSH (Medical Subject Headings) ingestor.

Pulls NLM's annual MeSH descriptor release and emits each descriptor
as a vocabulary term with its synonyms as `sounds_like` entries.

# Why MeSH

MeSH is the National Library of Medicine's authoritative controlled
vocabulary for medical and biological topics. Every PubMed citation
is indexed against MeSH headings, so it's the canonical source for:

  - Drug names (generic + brand, with synonyms)
  - Disease names (with ICD-aligned terminology)
  - Anatomical structures
  - Procedures + diagnostic tests
  - Medical specialties
  - Public-health concepts (HIPAA, EHR aren't MeSH but most clinical
    terms are)

~30,000 descriptors at the descriptor level, each with 1-15 entry
terms (synonyms). Total contributable terms: ~150-200k after dedup.

# Format

NLM publishes MeSH in three formats: XML (~360 MB, structured), ASCII
("d20XX.bin", ~60 MB, line-based), and RDF (large). We use the ASCII
format because it's smaller and simpler to parse line-by-line:

    *NEWRECORD
    RECTYPE = D
    MH = Acetaminophen
    AQ = AA AD AE AG ...
    ENTRY = APAP
    ENTRY = Hydroxyacetanilide
    ENTRY = N-Acetyl-p-aminophenol
    MN = D02.092.146.045.114
    ...
    ST = T109
    UI = D000082
    *NEWRECORD
    ...

# Filtering

Not every MeSH descriptor is useful in transcription. We filter to
the semantic types most likely to appear in spoken medical context:
  - T047 / T191 = disease, syndrome
  - T121 / T200 = drug, organic chemical
  - T060 / T061 = diagnostic procedure, therapeutic procedure
  - T023 / T029 = body part, anatomy
  - T184 = sign or symptom

# License

MeSH is in the public domain (US government work).

Source: https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/asciimesh/
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

from ..pronunciation import derive_acronym_pronunciations
from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = (
    Path(__file__).resolve().parents[2].parent
    / "assets" / "vocab_corpus_cache" / "mesh"
)
# Try the latest MeSH year first, fall back to prior years if NLM
# hasn't published yet for the current year.
_CANDIDATE_YEARS = (2025, 2024, 2023)
_BASE_URL = "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/asciimesh"

# Semantic type filter — limits to clinically-relevant terms. Skips
# things like "Geographic Locations" (T083) and "Behavior" categories
# that aren't useful for transcription correction.
_RELEVANT_SEMANTIC_TYPES = {
    # Diseases / conditions
    "T047",  # Disease or Syndrome
    "T191",  # Neoplastic Process
    "T046",  # Pathologic Function
    "T037",  # Injury or Poisoning
    "T048",  # Mental or Behavioral Dysfunction
    "T184",  # Sign or Symptom
    # Drugs / chemicals
    "T121",  # Pharmacologic Substance
    "T109",  # Organic Chemical
    "T200",  # Clinical Drug
    "T122",  # Biomedical or Dental Material
    "T123",  # Biologically Active Substance
    "T125",  # Hormone
    "T127",  # Vitamin
    "T129",  # Immunologic Factor
    "T130",  # Indicator, Reagent, or Diagnostic Aid
    # Procedures
    "T060",  # Diagnostic Procedure
    "T061",  # Therapeutic or Preventive Procedure
    "T058",  # Health Care Activity
    # Anatomy
    "T023",  # Body Part, Organ, or Organ Component
    "T024",  # Tissue
    "T029",  # Body Location or Region
    "T022",  # Body System
    # Microorganisms (clinically-relevant)
    "T007",  # Bacterium
    "T005",  # Virus
}


def _ensure_dataset() -> Path | None:
    """Download d20XX.bin into the cache if missing.

    Returns the path on success, None on network failure (the build
    will continue without MeSH terms — non-critical fallback).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import urllib.request
    import urllib.error

    # Reuse any cached file (most recent year wins).
    cached = sorted(CACHE_DIR.glob("d*.bin"), reverse=True)
    if cached:
        return cached[0]

    for year in _CANDIDATE_YEARS:
        url = f"{_BASE_URL}/d{year}.bin"
        target = CACHE_DIR / f"d{year}.bin"
        try:
            logger.info("Fetching MeSH %d from %s", year, url)
            req = urllib.request.Request(
                url, headers={"User-Agent": "verbatim-studio/corpus-build"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                target.write_bytes(resp.read())
            logger.info("MeSH %d downloaded (%d bytes)", year, target.stat().st_size)
            return target
        except urllib.error.HTTPError as e:
            logger.info("MeSH %d not yet available (HTTP %d)", year, e.code)
            continue
        except Exception as e:
            logger.warning("MeSH %d fetch failed: %s", year, e)
            continue

    logger.warning("All MeSH download attempts failed — skipping")
    return None


def _parse_records(path: Path) -> Iterable[dict]:
    """Yield each *NEWRECORD block as a dict of {field: list[value]}.

    The ASCII format uses `KEY = VALUE` lines with `*NEWRECORD` markers
    between blocks. Some keys repeat (ENTRY, ST), so we accumulate as
    lists.
    """
    record: dict = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line == "*NEWRECORD":
                if record:
                    yield record
                record = {}
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            record.setdefault(key, []).append(val)
        if record:
            yield record


def _is_acronym_shape(s: str) -> bool:
    """Heuristic: is this term spoken as letters or as a word?

    All-caps 2-8 chars → letter-by-letter (APAP, HIV, ACE).
    Mixed-case → word (Acetaminophen, Tylenol).
    """
    cleaned = s.strip()
    return bool(cleaned) and cleaned.isupper() and 2 <= len(cleaned) <= 8


def iter_terms() -> Iterable[RawTerm]:
    path = _ensure_dataset()
    if path is None:
        logger.warning("MeSH ingestor: no dataset available")
        return

    yielded = 0
    skipped_irrelevant_st = 0
    seen: set[str] = set()

    for record in _parse_records(path):
        if record.get("RECTYPE", [""])[0] != "D":
            continue
        mh = (record.get("MH") or [""])[0].strip()
        if not mh or len(mh) < 2:
            continue

        # Filter by semantic type — keep only clinically-relevant
        sts = set(record.get("ST", []))
        if not sts.intersection(_RELEVANT_SEMANTIC_TYPES):
            skipped_irrelevant_st += 1
            continue

        # ENTRY lines are synonyms. Some have qualifier suffixes after
        # `|` (e.g. "APAP|T109|abc..."). Strip everything after the pipe.
        entries: list[str] = []
        for raw in record.get("ENTRY", []):
            syn = raw.split("|", 1)[0].strip()
            if syn and syn != mh and len(syn) < 60:
                entries.append(syn)

        # Pronunciation hints — for acronym-shaped entries, derive
        # letter-by-letter forms; for word-shaped, just include the
        # synonym as a sounds_like (helps when Whisper hears the brand
        # name vs the generic, etc).
        sounds_like: list[str] = []
        for syn in entries[:10]:  # cap to avoid unbounded sounds_like
            if _is_acronym_shape(syn):
                sounds_like.extend(derive_acronym_pronunciations(syn))
            else:
                sounds_like.append(syn.lower())
        # Also derive for the canonical itself if it's acronym-shaped
        if _is_acronym_shape(mh):
            sounds_like = derive_acronym_pronunciations(mh) + sounds_like

        # Dedupe sounds_like preserving order
        sl_seen: set[str] = set()
        sounds_like_unique: list[str] = []
        for s in sounds_like:
            sl = s.strip().lower()
            if sl and sl not in sl_seen:
                sl_seen.add(sl)
                sounds_like_unique.append(s)

        # Popularity scoring:
        # - Drugs (T121, T109, T200): 0.7 (commonly spoken)
        # - Diseases (T047, T191): 0.7
        # - Procedures (T060, T061): 0.65
        # - Anatomy: 0.55 (most well-known terms Whisper handles fine)
        # - Other: 0.5
        if sts & {"T121", "T109", "T200", "T122", "T123"}:
            score = 0.7
        elif sts & {"T047", "T191", "T046", "T048", "T184"}:
            score = 0.7
        elif sts & {"T060", "T061", "T058"}:
            score = 0.65
        else:
            score = 0.5

        # Yield the canonical
        canonical = mh
        key = canonical.lower()
        if key not in seen:
            seen.add(key)
            yield RawTerm(
                term=canonical,
                canonical_form=canonical,
                category="medical",
                subcategory="mesh_descriptor",
                sounds_like=sounds_like_unique[:20],
                context_blurb=("MeSH: " + ",".join(sorted(sts))[:100])[:140],
                popularity_score=score,
                source="MeSH (NLM, public domain)",
            )
            yielded += 1

        # Also yield each entry term as its own row, with the canonical
        # as a sounds_like — that way "APAP" matches whether Whisper
        # outputs "APAP" or "Acetaminophen".
        for syn in entries:
            syn_key = syn.lower()
            if syn_key in seen or len(syn) < 2:
                continue
            seen.add(syn_key)
            # Synonyms get a slightly lower popularity than the descriptor
            syn_sl = [canonical.lower()]
            if _is_acronym_shape(syn):
                syn_sl = derive_acronym_pronunciations(syn) + syn_sl
            yield RawTerm(
                term=syn,
                canonical_form=syn,
                category="medical",
                subcategory="mesh_synonym",
                sounds_like=syn_sl[:15],
                context_blurb=f"MeSH synonym for {canonical}"[:140],
                popularity_score=score - 0.1,
                source="MeSH (NLM, public domain)",
            )
            yielded += 1

    logger.info(
        "MeSH: %d terms yielded (%d skipped — irrelevant semantic type)",
        yielded, skipped_irrelevant_st,
    )


name = "MeSH (Medical Subject Headings)"
category = "medical"
