"""GeoNames cities + countries.

CC-BY 4.0. Names of cities >5,000 population worldwide (~50k cities)
plus all country names. Whisper consistently mistranscribes
international city names ("Reykjavík" → "raked the vic", "Ouagadougou"
→ "wagadugu"). Bundling these massively improves geo-aware
transcription quality.

Source: https://download.geonames.org/export/dump/cities5000.zip
        https://download.geonames.org/export/dump/countryInfo.txt
License: CC-BY 4.0 (attribute "GeoNames" in app About screen)
"""

from __future__ import annotations

import logging
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "vocab_corpus_cache" / "geonames"
CITIES_URL = "https://download.geonames.org/export/dump/cities5000.zip"
COUNTRIES_URL = "https://download.geonames.org/export/dump/countryInfo.txt"

# GeoNames cities5000.txt schema (per readme.txt):
# 0 geonameid, 1 name, 2 asciiname, 3 alternatenames, 4 latitude,
# 5 longitude, 6 feature_class, 7 feature_code, 8 country_code,
# 9 cc2, 10 admin1_code, ..., 14 population, ...

_FIELD_NAME = 1
_FIELD_ASCII = 2
_FIELD_COUNTRY = 8
_FIELD_POPULATION = 14


def _ensure_cities() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / "cities5000.txt"
    if target.exists():
        return target

    import urllib.request
    logger.info("Fetching GeoNames cities5000 from %s", CITIES_URL)
    req = urllib.request.Request(
        CITIES_URL,
        headers={"User-Agent": "VerbatimStudio-CorpusBuild/1.0"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        zip_bytes = resp.read()
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        with zf.open("cities5000.txt") as zf_in:
            target.write_bytes(zf_in.read())
    return target


def _ensure_countries() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / "countryInfo.txt"
    if target.exists():
        return target

    import urllib.request
    logger.info("Fetching GeoNames countryInfo from %s", COUNTRIES_URL)
    req = urllib.request.Request(
        COUNTRIES_URL,
        headers={"User-Agent": "VerbatimStudio-CorpusBuild/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        target.write_bytes(resp.read())
    return target


def _iter_cities() -> Iterable[RawTerm]:
    try:
        path = _ensure_cities()
    except Exception as e:
        logger.warning("GeoNames cities fetch failed: %s", e)
        return

    seen: set[str] = set()
    yielded = 0

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cols = line.split("\t")
        if len(cols) < 15:
            continue
        name = cols[_FIELD_NAME].strip()
        ascii_name = cols[_FIELD_ASCII].strip()
        country = cols[_FIELD_COUNTRY].strip()
        try:
            population = int(cols[_FIELD_POPULATION] or 0)
        except ValueError:
            population = 0

        # Use the ASCII form as the lookup; keep the original (possibly
        # diacritic-bearing) form as the canonical so Whisper can
        # produce the right spelling.
        canonical = name or ascii_name
        if not canonical or len(canonical) < 2:
            continue
        key = (ascii_name or canonical).lower()
        if key in seen:
            continue
        seen.add(key)

        # Population-based popularity. log scale: 1M+ → 0.9, 100k+ → 0.6,
        # 10k+ → 0.3, else → 0.1.
        if population >= 1_000_000:
            score = 0.9
        elif population >= 100_000:
            score = 0.6
        elif population >= 10_000:
            score = 0.3
        else:
            score = 0.1

        yield RawTerm(
            term=canonical,
            canonical_form=canonical,
            category="proper_nouns",
            subcategory="city",
            context_blurb=f"City in {country}, population {population:,}",
            popularity_score=score,
            source="GeoNames (CC-BY 4.0)",
        )
        yielded += 1

    logger.info("GeoNames cities5000: %d cities yielded", yielded)


def _iter_countries() -> Iterable[RawTerm]:
    try:
        path = _ensure_countries()
    except Exception as e:
        logger.warning("GeoNames countries fetch failed: %s", e)
        return

    yielded = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 5:
            continue
        # countryInfo schema: ISO ISO3 ISO-Numeric fips Country Capital ...
        country_name = cols[4].strip()
        capital = cols[5].strip() if len(cols) > 5 else ""
        if not country_name:
            continue
        yield RawTerm(
            term=country_name,
            canonical_form=country_name,
            category="proper_nouns",
            subcategory="country",
            context_blurb=f"Country, capital {capital}" if capital else "Country",
            popularity_score=0.95,  # Country names should always be primed
            source="GeoNames (CC-BY 4.0)",
        )
        yielded += 1
    logger.info("GeoNames countries: %d yielded", yielded)


def iter_terms() -> Iterable[RawTerm]:
    yield from _iter_countries()
    yield from _iter_cities()


name = "GeoNames"
category = "proper_nouns"
