"""OurAirports — IATA/ICAO codes + airport names.

Public-domain dataset of every airport worldwide (~80k entries). Whisper
mishears 3-letter IATA codes spoken individually ("J-F-K" → "J F K" or
"jay if kay"). Bundling both the codes and airport names helps both
spelled-out and named recognition.

Source: https://ourairports.com/data/
License: Public domain
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Iterable

from ..pronunciation import letter_by_letter
from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "vocab_corpus_cache" / "ourairports"
SOURCE_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"

# Skip closed/heliport entries — they add noise without recognition value.
_VALID_TYPES = {
    "large_airport", "medium_airport", "small_airport", "seaplane_base",
}


def _ensure_dataset() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / "airports.csv"
    if target.exists():
        return target

    import urllib.request
    logger.info("Fetching OurAirports from %s", SOURCE_URL)
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "VerbatimStudio-CorpusBuild/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        target.write_bytes(resp.read())
    return target


def iter_terms() -> Iterable[RawTerm]:
    try:
        path = _ensure_dataset()
    except Exception as e:
        logger.warning("OurAirports fetch failed: %s — emitting 0 terms", e)
        return

    text = path.read_text(encoding="utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    seen_iata: set[str] = set()
    seen_icao: set[str] = set()
    seen_names: set[str] = set()
    yielded = 0

    for row in reader:
        airport_type = (row.get("type") or "").strip()
        if airport_type not in _VALID_TYPES:
            continue

        name = (row.get("name") or "").strip()
        iata = (row.get("iata_code") or "").strip().upper()
        icao = (row.get("ident") or "").strip().upper()

        # Larger airports get higher popularity. The type field encodes
        # this directly.
        size_score = {
            "large_airport": 0.9,
            "medium_airport": 0.5,
            "small_airport": 0.2,
            "seaplane_base": 0.1,
        }[airport_type]

        if iata and len(iata) == 3 and iata not in seen_iata:
            seen_iata.add(iata)
            # Airport codes are spoken letter-by-letter ("J F K") — bake
            # both the spaced and hyphenated forms in so the post-pass
            # phonetic match catches "jay ef kay" → "JFK".
            sounds_like_iata = [
                letter_by_letter(iata, joiner=" "),
                letter_by_letter(iata, joiner="-"),
            ]
            yield RawTerm(
                term=iata,
                canonical_form=iata,
                category="aviation",
                subcategory="iata",
                sounds_like=sounds_like_iata,
                context_blurb=f"{name} IATA code",
                popularity_score=size_score,
                source="OurAirports (public domain)",
            )
            yielded += 1

        if icao and 3 <= len(icao) <= 4 and icao not in seen_icao and icao != iata:
            seen_icao.add(icao)
            sounds_like_icao = [
                letter_by_letter(icao, joiner=" "),
                letter_by_letter(icao, joiner="-"),
            ]
            yield RawTerm(
                term=icao,
                canonical_form=icao,
                category="aviation",
                subcategory="icao",
                sounds_like=sounds_like_icao,
                context_blurb=f"{name} ICAO code",
                popularity_score=size_score * 0.7,  # ICAO less commonly spoken
                source="OurAirports (public domain)",
            )
            yielded += 1

        # Trim "International Airport" / "Regional Airport" suffixes that
        # speakers usually drop.
        clean_name = name
        for suffix in (" International Airport", " Regional Airport", " Municipal Airport", " Airport"):
            if clean_name.endswith(suffix):
                clean_name = clean_name[: -len(suffix)]
                break
        clean_name = clean_name.strip()
        if clean_name and len(clean_name) >= 3 and clean_name.lower() not in seen_names:
            seen_names.add(clean_name.lower())
            yield RawTerm(
                term=clean_name,
                canonical_form=clean_name,
                category="aviation",
                subcategory="airport_name",
                context_blurb=f"Airport ({airport_type.replace('_', ' ')})",
                popularity_score=size_score,
                source="OurAirports (public domain)",
            )
            yielded += 1

    logger.info("OurAirports: %d terms yielded", yielded)


name = "OurAirports"
category = "aviation"
