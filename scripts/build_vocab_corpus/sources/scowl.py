"""SCOWL — Spell Checker Oriented Word Lists.

A 120K-word English-spelling reference. **Used by the corpus as the
"this is a normal English word" gate** — the runtime phonetic
correction service consults this set before replacing a word with a
dictionary term, preventing false-positive substitutions like
"advise" → "ADSEP". The English-words PyPI package this currently
relies on is bundled separately (~8 MB); SCOWL gives us a smaller,
more curated set we can compose with the bundled corpus without
shipping a third dictionary.

Source: https://github.com/en-wl/wordlist (SCOWL v2)
Mirror used: https://wordlist.aspell.net/dicts/
License: BSD-compatible / MIT-like (see SCOWL readme; explicit
permission for closed-source bundling).

# Storage approach

SCOWL words are *negative-gate* references — we don't want to bias
Whisper toward "the" or "and" or "have." So they're emitted with
category=`general` and subcategory=`scowl_negative_gate` so the
retrieval layer can EXCLUDE them from the prompt-bias candidate
set while still using them to verify "is this a real English word?"
in Phase 2 phonetic correction.
"""

from __future__ import annotations

import logging
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2].parent / "assets" / "vocab_corpus_cache" / "scowl"

# Use the prebuilt SCOWL Hunspell-style dictionary distribution. Tier 60
# is the canonical "default spellcheck" cutoff (~120k words for en-US)
# and matches what most Hunspell-using apps ship. 70 is "valid current
# usage" — adds another ~140k. We pick 60 to keep the gate tight.
SOURCE_URL = (
    "https://downloads.sourceforge.net/wordlist/"
    "scowl-2020.12.07.zip"
)


def _ensure_dataset() -> Path:
    """Download + unpack SCOWL final word lists.

    Resilient to upstream URL changes — caller can also drop the zip
    or extracted files into CACHE_DIR manually before build.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    extract_dir = CACHE_DIR / "scowl"
    if extract_dir.exists() and any(extract_dir.iterdir()):
        return extract_dir

    import urllib.request
    logger.info("Fetching SCOWL from %s", SOURCE_URL)
    req = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "VerbatimStudio-CorpusBuild/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            zip_bytes = resp.read()
    except Exception as e:
        logger.warning("SCOWL fetch failed: %s — emitting 0 terms", e)
        raise

    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        zf.extractall(extract_dir)
    return extract_dir


def iter_terms() -> Iterable[RawTerm]:
    """Yield SCOWL words at tier ≤60 from the en-US final files.

    SCOWL ships per-tier files like `final/english-words.10`,
    `english-words.20`, etc. Tiers ≤60 are the standard spellcheck
    cutoff. We aggregate them.
    """
    try:
        extract_dir = _ensure_dataset()
    except Exception:
        return

    # SCOWL extracts into a subdir like scowl-2020.12.07/final/.
    # Find the final/ subdir wherever it landed.
    final_dirs = list(extract_dir.glob("**/final"))
    if not final_dirs:
        logger.warning("SCOWL: no final/ directory found in %s", extract_dir)
        return
    final_dir = final_dirs[0]

    seen: set[str] = set()
    yielded = 0

    # Process en-US + en + abbreviations files at tiers 10, 20, 35, 40,
    # 50, 60. The list naming is "english-{type}.{tier}".
    for tier in (10, 20, 35, 40, 50, 60):
        for prefix in ("english-words", "english-abbreviations", "american-words"):
            path = final_dir / f"{prefix}.{tier}"
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line in text.splitlines():
                word = line.strip()
                if not word or len(word) < 2:
                    continue
                key = word.lower()
                if key in seen:
                    continue
                seen.add(key)

                # popularity_score ~ inverse of tier (higher tier = more obscure).
                # Tier 10 → 0.95; Tier 60 → 0.50.
                score = max(0.5, 1.0 - (tier / 100.0))

                yield RawTerm(
                    term=word,
                    canonical_form=word,
                    category="general",
                    subcategory=f"scowl_t{tier}",
                    context_blurb="",  # No blurb needed; English words don't add context value
                    popularity_score=score,
                    source="SCOWL (BSD-compatible)",
                )
                yielded += 1

    logger.info("SCOWL: %d unique words yielded (tiers ≤60)", yielded)


name = "SCOWL"
category = "general"
