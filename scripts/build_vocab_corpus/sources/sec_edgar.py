"""SEC EDGAR public company tickers + entity names.

SEC publishes the canonical company-ticker list as JSON (~10,000
current filers). All names are public-domain US gov data. Whisper
mishears most ticker-style references ("LULU" → "loo loo", "AMD" →
"and") and many less-common company names ("Lululemon", "ServiceNow",
"Cloudflare").

Source: https://www.sec.gov/files/company_tickers.json
License: Public domain (US federal work)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

from ..pronunciation import letter_by_letter
from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "vocab_corpus_cache" / "sec_edgar"
SOURCE_URL = "https://www.sec.gov/files/company_tickers.json"

# Common-English tickers that would over-trigger if added (e.g. "ON",
# "GO", "IT", "OR", "BE", "SO"). Not exhaustive — the runtime
# standard-English-word gate catches what we miss.
_NOISE_TICKERS = {
    "A", "AT", "BE", "BY", "DO", "GO", "IT", "IF", "IN", "IS", "ME",
    "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE",
}

# Suffixes to strip from canonical company names so we don't bias on
# legal-entity boilerplate. "Apple Inc." should become "Apple"; users
# say "Apple" not "Apple Inc".
_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(Inc\.?|LLC\.?|L\.?L\.?C\.?|Corp\.?|Corporation|Company|Co\.?|"
    r"Ltd\.?|Limited|Holdings|Group|PLC|S\.?A\.?|N\.?V\.?|AG|GmbH|"
    r"Trust|Fund|Partners|Holdings)\.?$",
    re.IGNORECASE,
)


def _ensure_dataset() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / "company_tickers.json"
    if target.exists() and target.stat().st_size > 1000:
        # Validate the cached file isn't garbage (a previous bad cache
        # left compressed bytes here). Quick sanity check on the prefix.
        if target.read_bytes()[:1] == b"{":
            return target
        logger.warning("Cached SEC EDGAR file is not JSON — re-fetching")
        target.unlink()

    import urllib.request
    logger.info("Fetching SEC EDGAR tickers from %s", SOURCE_URL)
    # SEC requires a User-Agent that identifies the requester per their
    # access policy. We DO NOT request gzip explicitly — urllib won't
    # transparently decode it and we'd have to handle the Content-
    # Encoding header by hand. Skipping gzip costs ~700 KB on this
    # particular endpoint (~3 MB uncompressed) which is fine.
    req = urllib.request.Request(
        SOURCE_URL,
        headers={
            "User-Agent": "Verbatim Studio Corpus Builder admin@verbatim.studio",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        target.write_bytes(resp.read())
    return target


def _clean_name(name: str) -> str:
    """Strip legal-entity boilerplate so canonical = how people say it."""
    name = _LEGAL_SUFFIX_RE.sub("", name).strip()
    # Some entities have multiple suffixes (e.g., "Foo Holdings, Inc.")
    name = _LEGAL_SUFFIX_RE.sub("", name).strip()
    return name.rstrip(",.")


def iter_terms() -> Iterable[RawTerm]:
    """Yield each EDGAR filer as two terms: the ticker + the company name."""
    try:
        path = _ensure_dataset()
    except Exception as e:
        logger.warning("SEC EDGAR fetch failed: %s — emitting 0 terms", e)
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("SEC EDGAR JSON parse failed: %s", e)
        return

    seen_tickers: set[str] = set()
    seen_names: set[str] = set()
    yielded_tickers = 0
    yielded_names = 0

    # Schema: {"0": {"cik_str": ..., "ticker": ..., "title": ...}, ...}
    for entry in data.values():
        ticker = (entry.get("ticker") or "").strip()
        title = (entry.get("title") or "").strip()

        if ticker and ticker not in _NOISE_TICKERS and ticker.upper() not in seen_tickers:
            seen_tickers.add(ticker.upper())
            # Tickers often spoken as letter-by-letter — short ones get
            # higher popularity since they're recurring (AMZN, AAPL).
            score = 0.6 if 2 <= len(ticker) <= 4 else 0.4
            ticker_upper = ticker.upper()
            sounds_like = [
                letter_by_letter(ticker_upper, joiner=" "),
                letter_by_letter(ticker_upper, joiner="-"),
            ]
            yield RawTerm(
                term=ticker_upper,
                canonical_form=ticker_upper,
                category="business",
                subcategory="ticker",
                sounds_like=sounds_like,
                context_blurb=f"Stock ticker for {_clean_name(title)}",
                popularity_score=score,
                source="SEC EDGAR (public domain)",
            )
            yielded_tickers += 1

        if title:
            clean = _clean_name(title)
            if not clean or len(clean) < 2:
                continue
            key = clean.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            yield RawTerm(
                term=clean,
                canonical_form=clean,
                category="business",
                subcategory="company",
                context_blurb=f"Public company; ticker {ticker}" if ticker else "Public company",
                popularity_score=0.5,
                source="SEC EDGAR (public domain)",
            )
            yielded_names += 1

    logger.info(
        "SEC EDGAR: %d tickers + %d company names yielded",
        yielded_tickers, yielded_names,
    )


name = "SEC EDGAR"
category = "business"
