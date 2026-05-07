"""Curated colloquial slang — internet, Gen Z, regional, AAVE-derived.

Whisper systematically mishears slang because its training corpus
underweights AAVE and post-2020 internet vernacular. The proper-noun
classifier won't auto-add these (they're not capitalized) and the
threshold counter would take too long. Hand-curating from kaikki.org
Wiktionary (CC-BY-SA) and the Wikipedia "Glossary of 2020s slang"
gives a high-precision starter set.

License: CC-BY-SA 4.0 (attribution: Wikipedia/Wiktionary contributors)
"""

from __future__ import annotations

import logging
from typing import Iterable

from ..types import RawTerm

logger = logging.getLogger(__name__)


# (canonical, gloss, subcategory) — written as users actually type/say.
_TERMS: list[tuple[str, str, str]] = [
    # Internet / texting
    ("TBH", "to be honest", "internet"),
    ("IMO", "in my opinion", "internet"),
    ("IMHO", "in my humble opinion", "internet"),
    ("NGL", "not gonna lie", "internet"),
    ("FOMO", "fear of missing out", "internet"),
    ("YOLO", "you only live once", "internet"),
    ("GOAT", "greatest of all time", "internet"),
    ("AF", "as fuck (intensifier)", "internet"),
    ("DM", "direct message", "internet"),
    ("RT", "retweet", "internet"),
    ("FTW", "for the win", "internet"),
    ("FTFY", "fixed that for you", "internet"),
    ("TIL", "today I learned", "internet"),
    ("TLDR", "too long, didn't read", "internet"),
    ("IIRC", "if I recall correctly", "internet"),
    ("IANAL", "I am not a lawyer", "internet"),
    ("YMMV", "your mileage may vary", "internet"),
    ("AMA", "ask me anything", "internet"),
    ("OP", "original poster", "internet"),
    ("BRB", "be right back", "internet"),
    ("AFK", "away from keyboard", "internet"),
    ("IRL", "in real life", "internet"),
    ("DAE", "does anyone else", "internet"),
    ("ELI5", "explain like I'm five", "internet"),
    # Modern usage
    ("rizz", "charisma; romantic appeal", "genz"),
    ("sus", "suspicious", "genz"),
    ("slay", "do something exceptionally well", "genz"),
    ("vibe", "feeling or atmosphere", "genz"),
    ("vibe check", "assessing someone's mood", "genz"),
    ("lit", "exciting; excellent", "genz"),
    ("mid", "mediocre", "genz"),
    ("no cap", "no lie; for real", "genz"),
    ("on god", "I swear", "genz"),
    ("bussin", "very good (esp. food)", "genz"),
    ("bussin'", "very good (esp. food)", "genz"),
    ("bet", "okay; agreed", "genz"),
    ("based", "admirably authentic", "genz"),
    ("cringe", "embarrassing", "genz"),
    ("yeet", "throw forcefully; expression of excitement", "genz"),
    ("salty", "bitter; resentful", "genz"),
    ("flex", "show off", "genz"),
    ("ghosting", "abruptly cutting off contact", "genz"),
    ("simp", "person showing excessive devotion", "genz"),
    ("stan", "extreme fan", "genz"),
    ("woke", "aware of social issues", "genz"),
    ("doomscroll", "obsessively scrolling negative news", "genz"),
    ("doomscrolling", "obsessively scrolling negative news", "genz"),
    ("brainrot", "mind-numbing content", "genz"),
    ("delulu", "delusional", "genz"),
    ("gyat", "expression of admiration (typically NSFW)", "genz"),
    ("skibidi", "absurd; meme-derived", "genz"),
    ("ick", "sudden feeling of repulsion", "genz"),
    ("Ohio", "weird, surreal, low-quality (meme)", "genz"),
    # Professional / workplace
    ("stand-up", "brief daily team meeting", "professional"),
    ("standup", "brief daily team meeting", "professional"),
    ("sync", "meeting to align", "professional"),
    ("sync-up", "meeting to align", "professional"),
    ("ping", "send a brief message", "professional"),
    ("loop in", "include in communication", "professional"),
    ("circle back", "return to a topic later", "professional"),
    ("touch base", "check in", "professional"),
    ("low-hanging fruit", "easy task with high return", "professional"),
    # AAVE-derived (long established in mainstream usage)
    ("y'all", "you all", "regional"),
    ("finna", "fixing to; about to", "regional"),
    ("bae", "significant other", "aave"),
    ("fam", "close friends", "aave"),
    ("lowkey", "subtly; somewhat", "aave"),
    ("highkey", "openly; very much", "aave"),
    ("tea", "gossip", "aave"),
    ("shade", "subtle disrespect", "aave"),
    ("throw shade", "subtly insult", "aave"),
    ("spill the tea", "share gossip", "aave"),
    ("on point", "exactly right", "aave"),
    ("clutch", "decisive in critical moments", "aave"),
    # Regional
    ("hella", "very (NorCal)", "regional"),
    ("wicked", "very (New England)", "regional"),
    ("bodega", "small corner store (NYC)", "regional"),
    ("y'all've", "you all have", "regional"),
    ("fixin'", "fixing; about to", "regional"),
    ("fixing to", "about to", "regional"),
    ("might could", "perhaps could", "regional"),
    ("over yonder", "over there", "regional"),
    # Older slang still in use
    ("groovy", "excellent (1960s-70s)", "older"),
    ("dope", "excellent", "older"),
    ("fly", "stylish", "older"),
    ("fresh", "new and stylish", "older"),
    # Common idioms Whisper sometimes misses
    ("smh", "shaking my head", "internet"),
    ("idk", "I don't know", "internet"),
    ("idc", "I don't care", "internet"),
    ("idgaf", "I don't give a fuck", "internet"),
    ("istg", "I swear to god", "internet"),
    ("imo", "in my opinion", "internet"),
    ("ngl", "not gonna lie", "internet"),
    ("ttyl", "talk to you later", "internet"),
    ("wbu", "what about you", "internet"),
    ("hbu", "how about you", "internet"),
    ("nvm", "never mind", "internet"),
    ("rly", "really", "internet"),
    ("prolly", "probably", "internet"),
    ("def", "definitely", "internet"),
    ("obvi", "obviously", "internet"),
    ("totes", "totally", "internet"),
]


def iter_terms() -> Iterable[RawTerm]:
    seen: set[str] = set()
    for canonical, gloss, subcat in _TERMS:
        key = canonical.lower()
        if key in seen:
            continue
        seen.add(key)
        # Slang is high-frequency in conversational audio — boost.
        score = 0.7
        yield RawTerm(
            term=canonical,
            canonical_form=canonical,
            category="slang",
            subcategory=subcat,
            context_blurb=gloss[:140],
            popularity_score=score,
            source="Curated colloquial slang (Wiktionary CC-BY-SA / Wikipedia 2020s glossary)",
        )
    logger.info("Slang: %d terms yielded", len(seen))


name = "Colloquial Slang"
category = "slang"
