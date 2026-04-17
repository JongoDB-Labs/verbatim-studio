"""Filler word detection service.

Detects verbal fillers (um, uh, like, you know, sort of, etc.) in transcript
segments using regex-based pattern matching with context awareness.  Returns
FillerMatch objects with character positions per segment.
"""

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class FillerMatch:
    """A single detected filler word or phrase within a segment."""

    word: str
    start_char: int
    end_char: int
    filler_type: str  # "single", "phrase", or "context"


# ---------------------------------------------------------------------------
# Filler word lists
# ---------------------------------------------------------------------------

SINGLE_FILLERS: list[str] = [
    "um", "uh", "uhm", "umm", "uhh",
    "er", "eh", "ah",
    "hmm", "hm", "mm", "mhm",
    "well", "so",
    "basically", "actually", "literally",
    "right",
]

PHRASE_FILLERS: list[str] = [
    "you know",
    "i mean",
    "sort of",
    "kind of",
    "i guess",
    "i suppose",
    "or something",
    "or whatever",
    "and stuff",
    "and everything",
]

# Subject pronouns / auxiliaries that precede "like" when it is used as a verb
# e.g.  "I like pizza", "they like swimming", "didn't like it"
_LIKE_VERB_PREDECESSORS = {
    "i", "we", "they", "you",
    "would", "didn't", "don't", "doesn't",
}

# ---------------------------------------------------------------------------
# Compiled patterns  (built once at import time)
# ---------------------------------------------------------------------------

# Word-boundary pattern for a literal phrase, case-insensitive.
# We need to handle start/end of string as well as word boundaries.


def _word_pattern(phrase: str) -> re.Pattern[str]:
    """Return a compiled regex that matches *phrase* at word boundaries."""
    escaped = re.escape(phrase)
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


_PHRASE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (phrase, _word_pattern(phrase)) for phrase in PHRASE_FILLERS
]

_SINGLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (word, _word_pattern(word)) for word in SINGLE_FILLERS
]

_LIKE_PATTERN: re.Pattern[str] = _word_pattern("like")

# Pattern to find the word immediately before "like"
_WORD_BEFORE_LIKE = re.compile(r"(\S+)\s+like(?!\w)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_like_verb(text: str, match_start: int) -> bool:
    """Return True if "like" at *match_start* is probably a verb (not a filler).

    Heuristic: if the word immediately preceding "like" is a subject pronoun
    or certain auxiliaries, treat it as a verb.
    """
    # Grab everything before the match to find the preceding word.
    before = text[:match_start].rstrip()
    if not before:
        return False
    # Last whitespace-delimited token before "like"
    preceding_word = before.rsplit(None, 1)[-1].lower().strip(".,!?;:'\"")
    return preceding_word in _LIKE_VERB_PREDECESSORS


def _overlaps(existing: list[FillerMatch], start: int, end: int) -> bool:
    """Return True if [start, end) overlaps with any existing match."""
    for m in existing:
        if start < m.end_char and end > m.start_char:
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_fillers(segments: list[dict]) -> dict[str, list[FillerMatch]]:
    """Detect filler words in transcript segments.

    Parameters
    ----------
    segments:
        List of segment dicts, each having at least ``id`` and ``text`` keys.

    Returns
    -------
    dict mapping segment_id to a list of :class:`FillerMatch` objects.
    Segments with no fillers (or missing/empty text) are omitted from the dict.
    """
    results: dict[str, list[FillerMatch]] = {}

    for seg in segments:
        seg_id = seg.get("id")
        text = seg.get("text")
        if not seg_id or not text:
            continue

        matches: list[FillerMatch] = []

        # 1. Phrase fillers first (longer matches get priority)
        for phrase, pattern in _PHRASE_PATTERNS:
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()
                if not _overlaps(matches, start, end):
                    matches.append(
                        FillerMatch(
                            word=text[start:end].lower(),
                            start_char=start,
                            end_char=end,
                            filler_type="phrase",
                        )
                    )

        # 2. Single-word fillers
        for word, pattern in _SINGLE_PATTERNS:
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()
                if not _overlaps(matches, start, end):
                    matches.append(
                        FillerMatch(
                            word=text[start:end].lower(),
                            start_char=start,
                            end_char=end,
                            filler_type="single",
                        )
                    )

        # 3. Context-dependent: "like"
        for m in _LIKE_PATTERN.finditer(text):
            start, end = m.start(), m.end()
            if _overlaps(matches, start, end):
                continue
            if _is_like_verb(text, start):
                continue
            matches.append(
                FillerMatch(
                    word=text[start:end].lower(),
                    start_char=start,
                    end_char=end,
                    filler_type="context",
                )
            )

        if matches:
            # Sort by position for predictable output
            matches.sort(key=lambda m: m.start_char)
            results[seg_id] = matches

    return results
