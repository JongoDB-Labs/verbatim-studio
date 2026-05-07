"""Wikidata SPARQL — top-N notable entities (people, orgs, brands).

Wikidata's CC0 license is the cleanest source for proper-noun lists.
We hit the public SPARQL endpoint with a query that returns the
N-most-linked entities by sitelink count (a proxy for global notability),
restricted to a few high-impact entity types: humans (Q5), companies
(Q4830453), product/software (Q7889 + Q386724), and military units
(Q176799).

Source: https://query.wikidata.org/
License: CC0 (Wikidata structured data)
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from pathlib import Path
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "vocab_corpus_cache" / "wikidata"
ENDPOINT = "https://query.wikidata.org/sparql"

# Per Wikidata's etiquette, queries should set a User-Agent identifying
# the bot/build for rate-limit accounting. Their docs ask for an email.
USER_AGENT = "VerbatimStudio-CorpusBuild/1.0 (https://verbatim.studio; admin@verbatim.studio)"

# Per-query result cap. Wikidata's public endpoint times out around 60s
# / 30k rows. We pull in batches per entity type.
QUERY_LIMIT = 5000


# Each entry: (subcategory, Wikidata Q-number for "instance of", popularity floor)
ENTITY_QUERIES = [
    # Notable humans (politicians, scientists, athletes, performers).
    ("notable_humans", "Q5", 0.4),
    # Companies / corporations.
    ("companies", "Q4830453", 0.5),
    # Software products.
    ("software", "Q7889", 0.4),
    # Films.
    ("films", "Q11424", 0.4),
    # Television series.
    ("tv_series", "Q5398426", 0.4),
    # Video games.
    ("video_games", "Q7889", 0.3),  # Q7889 is "computer program"; cover both
    # Music: solo musicians and bands separately.
    ("musicians", "Q177220", 0.5),
    ("bands", "Q215380", 0.5),
    # Military units.
    ("military_units", "Q176799", 0.3),
]


def _query_path(subcategory: str) -> Path:
    return CACHE_DIR / f"wikidata_{subcategory}.json"


def _run_sparql(subcategory: str, q_number: str) -> list[dict]:
    """Hit the Wikidata SPARQL endpoint for a single entity-type query.

    Caches the JSON response so subsequent runs don't re-query.
    """
    path = _query_path(subcategory)
    if path.exists() and path.stat().st_size > 100:
        return json.loads(path.read_text(encoding="utf-8")).get("results", {}).get("bindings", [])

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Order by sitelink count descending so we get the most-globally-
    # known entities first.
    query = f"""
    SELECT ?item ?itemLabel ?sitelinks WHERE {{
      ?item wdt:P31/wdt:P279* wd:{q_number} .
      ?item wikibase:sitelinks ?sitelinks .
      FILTER(?sitelinks > 5)
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
    }}
    ORDER BY DESC(?sitelinks)
    LIMIT {QUERY_LIMIT}
    """

    import urllib.request
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    logger.info("Wikidata SPARQL query: %s (Q=%s)", subcategory, q_number)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/sparql-results+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except Exception as e:
        logger.warning("Wikidata query %s failed: %s", subcategory, e)
        return []

    path.write_bytes(data)
    return json.loads(data).get("results", {}).get("bindings", [])


def iter_terms() -> Iterable[RawTerm]:
    """Yield labels for top-N notable entities across multiple types."""

    # Map subcategory → category for our taxonomy. Entertainment-side
    # (films, TV, games, music) lands under entertainment; humans land
    # under proper_nouns; companies under business; military_units
    # under military.
    cat_map = {
        "notable_humans": ("proper_nouns", "person"),
        "companies": ("business", "company"),
        "software": ("tech", "software"),
        "films": ("entertainment", "film"),
        "tv_series": ("entertainment", "tv_series"),
        "video_games": ("entertainment", "video_game"),
        "musicians": ("entertainment", "musician"),
        "bands": ("entertainment", "band"),
        "military_units": ("military", "unit"),
    }

    seen: set[str] = set()
    total = 0

    for subcategory, q_number, base_score in ENTITY_QUERIES:
        bindings = _run_sparql(subcategory, q_number)
        category, subcat = cat_map.get(subcategory, ("proper_nouns", subcategory))

        for binding in bindings:
            label = binding.get("itemLabel", {}).get("value", "").strip()
            if not label or len(label) < 2:
                continue
            # Skip Wikidata Q-IDs leaking through (when no English label exists)
            if label.startswith("Q") and label[1:].isdigit():
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)

            sitelinks = int(binding.get("sitelinks", {}).get("value", 0))
            # Sitelink count → popularity. >100 sitelinks = household name.
            if sitelinks >= 100:
                score = min(1.0, base_score + 0.4)
            elif sitelinks >= 30:
                score = base_score + 0.2
            elif sitelinks >= 10:
                score = base_score + 0.1
            else:
                score = base_score

            yield RawTerm(
                term=label,
                canonical_form=label,
                category=category,
                subcategory=subcat,
                context_blurb=f"Wikidata {subcat} ({sitelinks} sitelinks)",
                popularity_score=min(1.0, score),
                source="Wikidata (CC0)",
            )
            total += 1

    logger.info("Wikidata entities: %d unique labels yielded across %d queries",
                total, len(ENTITY_QUERIES))


name = "Wikidata Entities"
category = "proper_nouns"  # mixed; orchestrator dispatches per-row
