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

~30,000 descriptors, each with 1-15 entry terms (synonyms). After
filtering to clinically-relevant semantic types and dedup we typically
yield ~80-150k terms.

# Format

NLM publishes MeSH as compressed XML (`desc{YEAR}.gz`, ~50 MB
compressed, ~360 MB uncompressed). We stream-parse with iterparse to
avoid loading the whole tree.

  <DescriptorRecord>
    <DescriptorUI>D000082</DescriptorUI>
    <DescriptorName><String>Acetaminophen</String></DescriptorName>
    <ConceptList>
      <Concept>
        <TermList>
          <Term><String>Acetaminophen</String></Term>
          <Term><String>APAP</String></Term>
          <Term><String>Hydroxyacetanilide</String></Term>
        </TermList>
      </Concept>
    </ConceptList>
    <SemanticTypeList>
      <SemanticType>
        <SemanticTypeUI>T109</SemanticTypeUI>
        <SemanticTypeName>Organic Chemical</SemanticTypeName>
      </SemanticType>
    </SemanticTypeList>
  </DescriptorRecord>

# Filtering

Filter to clinically-relevant semantic types (diseases, drugs,
procedures, anatomy, signs/symptoms, microorganisms). Skips locations,
behaviors, occupational concepts.

# License

MeSH is in the public domain (US government work).

Source: https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/
"""

from __future__ import annotations

import gzip
import logging
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from ..pronunciation import derive_acronym_pronunciations
from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = (
    Path(__file__).resolve().parents[2].parent
    / "assets" / "vocab_corpus_cache" / "mesh"
)
# Try the latest MeSH year first (2026 is current as of 2025-11). Fall
# back to prior years if NLM hasn't published yet for the current year.
_CANDIDATE_YEARS = (2026, 2025, 2024)
_BASE_URL = "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh"

# MeSH tree number filter — clinically-relevant trees only.
# Top-level letter prefixes correspond to:
#   A — Anatomy
#   B — Organisms (we want pathogens, B01-B05)
#   C — Diseases
#   D — Chemicals and Drugs
#   E — Analytical, Diagnostic, and Therapeutic Techniques
#   F — Psychiatry and Psychology (F03 = Mental Disorders)
#   N — Health Care (N02 = administration, N03 = quality, etc — skip)
#   Z — Geographicals (skip)
_RELEVANT_TREE_PREFIXES = (
    "A",       # Anatomy (whole tree)
    "B01", "B02", "B03", "B04", "B05",  # Pathogens
    "C",       # All diseases
    "D",       # All drugs/chemicals
    "E01", "E02", "E03", "E04", "E05",  # Procedures + diagnostics + therapeutics
    "F03",     # Mental disorders
)


def _tree_relevant(tree_numbers: list[str]) -> bool:
    """Return True if any tree number falls in our clinically-relevant set."""
    for tn in tree_numbers:
        for prefix in _RELEVANT_TREE_PREFIXES:
            if tn.startswith(prefix):
                return True
    return False


def _ensure_dataset() -> Path | None:
    """Download desc{YEAR}.gz into the cache if missing."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import urllib.request
    import urllib.error

    cached = sorted(CACHE_DIR.glob("desc*.gz"), reverse=True)
    if cached and cached[0].stat().st_size > 1_000_000:
        return cached[0]

    for year in _CANDIDATE_YEARS:
        url = f"{_BASE_URL}/desc{year}.gz"
        target = CACHE_DIR / f"desc{year}.gz"
        try:
            logger.info("Fetching MeSH %d from %s", year, url)
            req = urllib.request.Request(
                url, headers={"User-Agent": "verbatim-studio/corpus-build"}
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
            if len(data) < 1_000_000:
                logger.info(
                    "MeSH %d at %s returned %d bytes — likely 404 redirect, skipping",
                    year, url, len(data),
                )
                continue
            target.write_bytes(data)
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


def _is_acronym_shape(s: str) -> bool:
    """Heuristic: is this term spoken as letters or as a word?"""
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

    # Stream-parse the gzipped XML so we don't load all 360 MB into memory.
    with gzip.open(path, "rb") as f:
        # iterparse yields events as elements close. We collect each
        # DescriptorRecord and clear it after to keep memory bounded.
        context = ET.iterparse(f, events=("end",))
        for event, elem in context:
            if elem.tag != "DescriptorRecord":
                continue

            # Extract tree numbers — filter to clinically-relevant trees
            tree_numbers: list[str] = []
            for tn in elem.findall(".//TreeNumber"):
                if tn.text:
                    tree_numbers.append(tn.text.strip())
            if not tree_numbers or not _tree_relevant(tree_numbers):
                skipped_irrelevant_st += 1
                elem.clear()
                continue

            # Canonical name
            name_elem = elem.find("./DescriptorName/String")
            if name_elem is None or not name_elem.text:
                elem.clear()
                continue
            mh = name_elem.text.strip()
            if not mh or len(mh) < 2:
                elem.clear()
                continue

            # Synonyms (entry terms)
            entries: list[str] = []
            for term_str in elem.findall(".//Concept/TermList/Term/String"):
                if term_str.text:
                    syn = term_str.text.strip()
                    if syn and syn != mh and 2 <= len(syn) <= 60:
                        entries.append(syn)

            # Pronunciation hints
            sounds_like: list[str] = []
            for syn in entries[:10]:  # cap to avoid bloated sounds_like
                if _is_acronym_shape(syn):
                    sounds_like.extend(derive_acronym_pronunciations(syn))
                else:
                    sounds_like.append(syn.lower())
            if _is_acronym_shape(mh):
                sounds_like = derive_acronym_pronunciations(mh) + sounds_like

            # Dedupe sounds_like
            sl_seen: set[str] = set()
            sounds_like_unique: list[str] = []
            for s in sounds_like:
                sl = s.strip().lower()
                if sl and sl not in sl_seen:
                    sl_seen.add(sl)
                    sounds_like_unique.append(s)

            # Popularity scoring based on tree number prefix
            primary_tree = tree_numbers[0] if tree_numbers else ""
            if primary_tree.startswith("D"):
                score = 0.7  # Drugs / chemicals — frequently spoken
            elif primary_tree.startswith("C"):
                score = 0.7  # Diseases
            elif primary_tree.startswith("E"):
                score = 0.65  # Procedures
            elif primary_tree.startswith("F03"):
                score = 0.7  # Mental disorders
            else:
                score = 0.5  # Anatomy, organisms

            # Yield the canonical descriptor
            key = mh.lower()
            if key not in seen:
                seen.add(key)
                yield RawTerm(
                    term=mh,
                    canonical_form=mh,
                    category="medical",
                    subcategory="mesh_descriptor",
                    sounds_like=sounds_like_unique[:20],
                    context_blurb=("MeSH: " + ",".join(tree_numbers[:3])[:100])[:140],
                    popularity_score=score,
                    source="MeSH (NLM, public domain)",
                )
                yielded += 1

            # Yield each synonym so "APAP" matches as well as "Acetaminophen"
            for syn in entries:
                syn_key = syn.lower()
                if syn_key in seen or len(syn) < 2:
                    continue
                seen.add(syn_key)
                syn_sl = [mh.lower()]
                if _is_acronym_shape(syn):
                    syn_sl = derive_acronym_pronunciations(syn) + syn_sl
                yield RawTerm(
                    term=syn,
                    canonical_form=syn,
                    category="medical",
                    subcategory="mesh_synonym",
                    sounds_like=syn_sl[:15],
                    context_blurb=f"MeSH synonym for {mh}"[:140],
                    popularity_score=score - 0.1,
                    source="MeSH (NLM, public domain)",
                )
                yielded += 1

            elem.clear()

    logger.info(
        "MeSH: %d terms yielded (%d skipped — irrelevant semantic type)",
        yielded, skipped_irrelevant_st,
    )


name = "MeSH (Medical Subject Headings)"
category = "medical"
