"""CMU Pronouncing Dictionary — homophone pair generator.

CMUdict (BSD/public-domain) contains ARPAbet phoneme strings for ~134k
English words. Two words sharing identical phoneme sequences are
homophones — the exact class Whisper confuses most reliably (their /
there / they're, peak / peek / pique). We don't ship the full CMUdict
(~3.8 MB of phoneme strings would just bloat the bundle); we ship the
homophone PAIRS as a compact correction-seed list.

These don't get added to the prompt (Whisper picks one homophone or
the other based on context). They're seeded into the
`misrecognition_seeds` category for the LLM cleanup pass to use as
"these are commonly confused" hints when context disambiguates.

Source: https://github.com/cmusphinx/cmudict
License: BSD / public domain
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "vocab_corpus_cache" / "cmudict"
SOURCE_URL = "https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict"

# Common-word homophones we want to pre-seed even before downloading
# the full CMUdict. These are the high-frequency confusions Whisper
# makes most often.
_KNOWN_PAIRS: list[tuple[str, str]] = [
    ("their", "there"), ("their", "they're"), ("there", "they're"),
    ("its", "it's"),
    ("your", "you're"),
    ("to", "too"), ("to", "two"), ("too", "two"),
    ("affect", "effect"),
    ("accept", "except"),
    ("principal", "principle"),
    ("compliment", "complement"),
    ("stationary", "stationery"),
    ("peak", "peek"), ("peak", "pique"),
    ("right", "write"), ("right", "rite"), ("write", "rite"),
    ("here", "hear"),
    ("buy", "by"), ("buy", "bye"), ("by", "bye"),
    ("flour", "flower"),
    ("flair", "flare"),
    ("waist", "waste"),
    ("week", "weak"),
    ("plain", "plane"),
    ("rain", "reign"), ("rain", "rein"), ("reign", "rein"),
    ("sea", "see"),
    ("son", "sun"),
    ("won", "one"),
    ("knight", "night"),
    ("knew", "new"),
    ("know", "no"),
    ("read", "red"),
    ("road", "rode"), ("road", "rowed"),
    ("brake", "break"),
    ("dear", "deer"),
    ("die", "dye"),
    ("hour", "our"),
    ("idle", "idol"),
    ("mail", "male"),
    ("meet", "meat"),
    ("medal", "metal"),
    ("morning", "mourning"),
    ("sole", "soul"),
    ("steel", "steal"),
    ("tail", "tale"),
    ("threw", "through"),
    ("waist", "waste"),
    ("weather", "whether"),
    ("which", "witch"),
    ("would", "wood"),
    ("ate", "eight"),
    ("bare", "bear"),
    ("bear", "bare"),
    ("blew", "blue"),
    ("board", "bored"),
    ("brake", "break"),
    ("carat", "carrot"),
    ("cell", "sell"),
    ("cent", "scent"), ("cent", "sent"),
    ("cite", "sight"), ("cite", "site"),
    ("course", "coarse"),
    ("days", "daze"),
    ("fair", "fare"),
    ("fairy", "ferry"),
    ("fined", "find"),
    ("for", "four"), ("for", "fore"),
    ("foul", "fowl"),
    ("genes", "jeans"),
    ("groan", "grown"),
    ("guessed", "guest"),
    ("hair", "hare"),
    ("hangar", "hanger"),
    ("haul", "hall"),
    ("heal", "heel"),
    ("higher", "hire"),
    ("hoarse", "horse"),
    ("hole", "whole"),
    ("knot", "not"),
    ("lessen", "lesson"),
    ("loan", "lone"),
    ("made", "maid"),
    ("main", "mane"),
    ("missed", "mist"),
    ("none", "nun"),
    ("oar", "or"), ("oar", "ore"), ("or", "ore"),
    ("pail", "pale"),
    ("pain", "pane"),
    ("pair", "pare"), ("pair", "pear"),
    ("pause", "paws"),
    ("pi", "pie"),
    ("piece", "peace"),
    ("plum", "plumb"),
    ("pole", "poll"),
    ("pour", "pore"),
    ("praise", "prays"), ("praise", "preys"),
    ("rapped", "wrapped"),
    ("ring", "wring"),
    ("role", "roll"),
    ("rose", "rows"),
    ("sale", "sail"),
    ("seam", "seem"),
    ("seas", "sees"), ("seas", "seize"),
    ("serial", "cereal"),
    ("shore", "sure"),
    ("some", "sum"),
    ("stair", "stare"),
    ("stake", "steak"),
    ("steal", "steel"),
    ("toad", "towed"),
    ("toe", "tow"),
    ("vain", "vane"), ("vain", "vein"),
    ("ware", "wear"), ("ware", "where"),
    ("way", "weigh"),
    ("we", "wee"),
    ("wood", "would"),
    ("yoke", "yolk"),
    ("you", "yew"), ("you", "ewe"),
    ("you'll", "yule"),
]


def _ensure_dataset() -> Path | None:
    """Download cmudict.dict if available; failure is non-fatal."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / "cmudict.dict"
    if target.exists():
        return target

    import urllib.request
    logger.info("Fetching CMUdict from %s", SOURCE_URL)
    try:
        req = urllib.request.Request(
            SOURCE_URL,
            headers={"User-Agent": "VerbatimStudio-CorpusBuild/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            target.write_bytes(resp.read())
    except Exception as e:
        logger.warning("CMUdict fetch failed: %s — using known-pairs only", e)
        return None
    return target


def _generate_pairs_from_cmudict(path: Path) -> list[tuple[str, str]]:
    """Walk CMUdict, group by phoneme sequence, emit pairs from each group.

    Skips uppercase words (initialisms / abbreviations) since those are
    not natural-speech homophones. Drops alternate-pronunciation marks
    "(2)", "(3)" so words with multiple recorded pronunciations dedupe.
    """
    pron_to_words: dict[str, list[str]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(";;;") or not line.strip():
            continue
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        word, phonemes = parts
        # Drop "(2)" alternate-pron suffix
        if "(" in word:
            word = word.split("(", 1)[0]
        word = word.lower()
        # Skip apostrophe-rich tokens; we already have the most common.
        if any(c.isdigit() or c in ".,?!" for c in word):
            continue
        # Strip stress markers from phonemes for looser matching
        # ("AA1 R" and "AA2 R" should homophone-match).
        ph_key = " ".join(p.rstrip("0123456789") for p in phonemes.split())
        pron_to_words[ph_key].append(word)

    pairs: list[tuple[str, str]] = []
    for words in pron_to_words.values():
        unique = sorted(set(words))
        if len(unique) < 2:
            continue
        # All pairs from this homophone group.
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                pairs.append((unique[i], unique[j]))
    return pairs


def iter_terms() -> Iterable[RawTerm]:
    seen: set[tuple[str, str]] = set()

    # Ship the known-good seed list always.
    pairs: list[tuple[str, str]] = list(_KNOWN_PAIRS)

    # Augment with CMUdict-generated pairs when available.
    path = _ensure_dataset()
    if path:
        try:
            pairs.extend(_generate_pairs_from_cmudict(path))
        except Exception as e:
            logger.warning("CMUdict parse failed: %s — using seeds only", e)

    yielded = 0
    for a, b in pairs:
        key = tuple(sorted([a.lower(), b.lower()]))
        if key in seen:
            continue
        seen.add(key)
        yield RawTerm(
            term=f"{key[0]}|{key[1]}",
            canonical_form=f"{key[0]}|{key[1]}",
            category="misrecognition_seeds",
            subcategory="homophone_pair",
            context_blurb=f"Homophones: {key[0]} / {key[1]}",
            popularity_score=0.3,
            source="CMUdict (BSD) + curated homophone pairs",
        )
        yielded += 1

    logger.info("CMUdict homophones: %d unique pairs yielded", yielded)


name = "CMUdict Homophones"
category = "misrecognition_seeds"
