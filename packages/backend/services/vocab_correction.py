"""Phonetic post-correction for domain-specific vocabulary.

Repairs Whisper misrecognitions of acronyms, proper nouns, and brand
names by matching low-confidence words against the user's custom
dictionary using Double Metaphone phonetic codes + edit distance.

# Why this exists

Even with priority-aware prompt biasing (Phase 1), Whisper still loses
some terms — the BPE tokenizer fragments rare strings, the language-
model bias of the decoder pulls them toward more probable English
spellings, and prompt budget is bounded at 224 tokens. The post-pass
repairs what biasing missed.

# The 3-gate test (additive, not destructive)

Pure phonetic match without gating is destructive: it replaces normal
English words with dictionary terms that just happen to share a
phonetic code. We require ALL three gates to fire before substituting:

  1. **Low Whisper confidence.** WhisperX's per-word alignment scores
     (whisperx.align()) are excellent at flagging uncertain words. We
     only consider replacement when confidence is below a threshold
     (default 0.6). High-confidence words are trusted.

  2. **Word is not standard English.** A 234k-entry English wordlist.
     If the word is in the dictionary, it's almost certainly correct as
     transcribed — we don't replace "advise" with "ADSEP" just because
     they share a phonetic prefix.

  3. **Phonetic match within bounded edit distance.** Double Metaphone
     code matches one of the user's dictionary terms (or one of its
     `sounds_like` alternates), AND the Levenshtein distance is small
     enough that the spellings could plausibly be the same word
     (default: edit distance ≤ ceil(len/2.5)).

When all three fire, we replace the word and record the correction in
an audit trail. The user can see and revert each correction.

# Failure modes we deliberately handle

- **Acronyms vs. words.** "MCTSSA" pronounced as letters
  (em-see-tee-double-s-ay) encodes differently than as a word.
  Mitigation: dictionary entries can include `sounds_like` alternates
  that capture the spoken form. Both encodings then map to the same
  canonical term.
- **Multi-word terms.** "north star metric" → 3 tokens. The current
  implementation handles single-word matches; multi-word phrases need
  segmentation logic that's deferred to Phase 3 (LLM correction).
- **Capitalization preservation.** When we replace "mctissa" with
  "MCTSSA", we keep the dictionary term's casing (proper noun /
  acronym intent).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

logger = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────────


# Three semantic stops, exposed in settings UI as Conservative / Default /
# Aggressive. Modeled on AssemblyAI's three-level boost. Single source of
# truth for the per-word confidence threshold below which we even consider
# replacement. Lower threshold = more permissive (more potential
# corrections, more risk of false positives).
CONFIDENCE_THRESHOLDS = {
    "conservative": 0.40,
    "default":      0.60,
    "aggressive":   0.75,
}

# Maximum allowed Levenshtein distance between candidate word and
# dictionary term, computed as ceil(len/MAX_EDIT_RATIO). Tighter ratio =
# only nearly-identical spellings replace.
MAX_EDIT_RATIO = 2.5

# Minimum word length to consider for replacement. Two-letter words
# (it, is, of) match too many things phonetically.
MIN_WORD_LEN = 3


# ── Lazy initialization of expensive resources ───────────────────────


@lru_cache(maxsize=1)
def _get_english_wordlist() -> frozenset[str]:
    """Load and cache the standard English wordlist.

    Returns a frozen set of ~234k lowercase English words. Used as the
    second gate in the 3-gate test — words present here are NOT replaced
    even if they phonetically match a dictionary term.

    On import failure (package missing), returns an empty set, which
    effectively disables the gate. Without the gate, the correction pass
    falls back to Phase 1 prompt biasing only — an explicit log warns
    the user.
    """
    try:
        from english_words import get_english_words_set
        words = get_english_words_set(["web2"], lower=True)
        return frozenset(w for w in words if w.isascii() and w.isalpha())
    except Exception as e:
        logger.warning(
            "english_words package not available — phonetic correction "
            "will skip the standard-word gate (more false positives possible). "
            "Install with: pip install english-words. Error: %s",
            e,
        )
        return frozenset()


@lru_cache(maxsize=1)
def _have_metaphone() -> bool:
    try:
        from metaphone import doublemetaphone  # noqa: F401
        return True
    except ImportError:
        logger.warning(
            "metaphone package not available — phonetic correction is disabled. "
            "Install with: pip install metaphone."
        )
        return False


def _phonetic_codes(text: str) -> tuple[str, str]:
    """Return Double Metaphone (primary, alternate) codes for *text*.

    Returns ('', '') if metaphone isn't installed."""
    if not _have_metaphone():
        return ("", "")
    from metaphone import doublemetaphone
    primary, alternate = doublemetaphone(text)
    return (primary or "", alternate or "")


# ── Core data shapes ─────────────────────────────────────────────────


@dataclass
class TermPhoneticIndex:
    """Pre-computed phonetic codes for a single dictionary term.

    Built once per session from the user's dictionary; reused across
    every word in the transcript.

    `code_to_spellings` maps each phonetic code → list of source spellings
    that produced it (the canonical term and any sounds_like alternates).
    Edit-distance is measured against the *matched* spelling, not the
    canonical, so a sounds_like like "nyokey" matches a Whisper output
    of "nyokey" with edit distance 0 even though the canonical is
    "gnocchi" (which is a far phonetic neighbour).
    """

    term_id: str
    canonical: str  # the user-facing form, preserved on replacement
    code_to_spellings: dict[str, list[str]] = field(default_factory=dict)

    @property
    def codes(self) -> set[str]:
        return set(self.code_to_spellings.keys())

    def best_match(
        self, candidate_codes: tuple[str, str], candidate_lower: str
    ) -> tuple[str, int] | None:
        """Return (matched_spelling, edit_distance) for the closest matching
        spelling whose phonetic code matches one of the candidate codes,
        or None if no codes match. Caller still gates on max edit distance.
        """
        best: tuple[str, int] | None = None
        for code in candidate_codes:
            if not code:
                continue
            for spelling in self.code_to_spellings.get(code, ()):
                dist = _levenshtein(candidate_lower, spelling.lower())
                if best is None or dist < best[1]:
                    best = (spelling, dist)
        return best


@dataclass
class WordCorrection:
    """A single correction applied to one word in a transcript."""

    original: str
    replacement: str
    confidence_before: float
    edit_distance: int
    term_id: str
    segment_index: int
    word_index_in_segment: int


@dataclass
class CorrectionResult:
    """Outcome of running phonetic correction over a list of segments."""

    corrections: list[WordCorrection]
    standard_word_gate_skipped: bool = False  # if True, gate (b) was disabled

    @property
    def count(self) -> int:
        return len(self.corrections)


# ── Phonetic index ───────────────────────────────────────────────────


def build_phonetic_index(entries: Iterable) -> list[TermPhoneticIndex]:
    """Build a phonetic index from CustomDictionaryEntry objects.

    For each term we encode (a) the term itself and (b) every entry in
    its `sounds_like` list. Each phonetic code is mapped back to the
    spellings that produced it so edit-distance comparison can use the
    matched form (Speechmatics-style sounds_like preserves intent: a
    user-supplied "nyohki" matches Whisper's "nyokey" output with edit
    distance 1, not the 6+ distance to the canonical "gnocchi").
    """
    index: list[TermPhoneticIndex] = []
    for entry in entries:
        spellings: list[str] = [entry.term]
        spellings.extend(entry.sounds_like or [])

        code_to_spellings: dict[str, list[str]] = {}
        for spelling in spellings:
            primary, alt = _phonetic_codes(spelling)
            for code in (primary, alt):
                if not code:
                    continue
                code_to_spellings.setdefault(code, []).append(spelling)

        if not code_to_spellings:
            continue
        index.append(TermPhoneticIndex(
            term_id=entry.id or "",
            canonical=entry.term,
            code_to_spellings=code_to_spellings,
        ))
    return index


# ── 3-gate test ──────────────────────────────────────────────────────


def _levenshtein(a: str, b: str) -> int:
    """Standard Levenshtein distance. Used to bound how phonetically
    similar two strings can be before we trust the match — pure phonetic
    code equality is too lossy on its own (e.g. "mecca" and "MCTSSA"
    both encode similarly). Fast enough for transcript-scale work
    without a C extension."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for i, cb in enumerate(b, start=1):
        curr = [i] + [0] * len(a)
        for j, ca in enumerate(a, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def _strip_punctuation(word: str) -> tuple[str, str, str]:
    """Split a word into (leading_punct, core, trailing_punct).

    Whisper emits words with surrounding punctuation attached
    ("ADSEP," or "(MCTSSA)"). We need to match the core letters but
    preserve the surrounding punctuation when we substitute.
    """
    leading = ""
    trailing = ""
    i = 0
    while i < len(word) and not word[i].isalnum():
        leading += word[i]
        i += 1
    j = len(word)
    while j > i and not word[j - 1].isalnum():
        trailing = word[j - 1] + trailing
        j -= 1
    return leading, word[i:j], trailing


def _should_correct(
    raw_word: str,
    confidence: float,
    threshold: float,
    standard_words: frozenset[str],
    phonetic_index: list[TermPhoneticIndex],
    *,
    standard_word_gate_active: bool,
) -> tuple[TermPhoneticIndex, int] | None:
    """The 3-gate test. Returns the matching TermPhoneticIndex + edit
    distance if all gates pass, else None.

    Gates:
      1. Whisper confidence below threshold
      2. Word not in standard English dictionary
      3. Phonetic match to a dictionary term, with bounded edit distance

    All three must fire. Order matters for short-circuit performance.
    """
    # Gate 1 — confidence threshold
    if confidence is None or confidence > threshold:
        return None

    _, core, _ = _strip_punctuation(raw_word)
    if len(core) < MIN_WORD_LEN:
        return None

    core_lower = core.lower()

    # Gate 2 — not a normal English word.
    # If the wordlist isn't loaded, we skip this gate but log it once.
    if standard_word_gate_active and core_lower in standard_words:
        return None

    # Gate 3 — phonetic match + edit distance.
    candidate_codes = _phonetic_codes(core)
    if not candidate_codes[0] and not candidate_codes[1]:
        return None  # word doesn't encode (numbers, symbols, single letter)

    max_edit = max(1, math.ceil(len(core) / MAX_EDIT_RATIO))

    best: tuple[TermPhoneticIndex, int] | None = None
    for term in phonetic_index:
        match = term.best_match(candidate_codes, core_lower)
        if match is None:
            continue
        _matched_spelling, dist = match
        if dist > max_edit:
            continue
        if best is None or dist < best[1]:
            best = (term, dist)
    return best


# ── Public correction entry point ────────────────────────────────────


def correct_segments(
    segments: list,
    dictionary_entries: list,
    *,
    threshold_name: str = "default",
) -> CorrectionResult:
    """Run phonetic post-correction over a list of WhisperX segments.

    Mutates *segments* in place: matched words have their `text` and the
    word-level `text` field replaced with the dictionary canonical form.
    Each segment also accumulates a `corrections` field listing what was
    changed so the UI can surface a revert affordance.

    Args:
        segments: TranscriptionSegment objects with .text and .words
                  (each word a TranscriptionWord with .text, .confidence).
        dictionary_entries: CustomDictionaryEntry objects.
        threshold_name: One of CONFIDENCE_THRESHOLDS keys.

    Returns:
        CorrectionResult listing every applied correction.
    """
    if not dictionary_entries:
        return CorrectionResult(corrections=[])

    if not _have_metaphone():
        return CorrectionResult(corrections=[])

    phonetic_index = build_phonetic_index(dictionary_entries)
    if not phonetic_index:
        return CorrectionResult(corrections=[])

    threshold = CONFIDENCE_THRESHOLDS.get(
        threshold_name, CONFIDENCE_THRESHOLDS["default"]
    )

    standard_words = _get_english_wordlist()
    standard_word_gate_active = bool(standard_words)

    corrections: list[WordCorrection] = []

    for seg_idx, seg in enumerate(segments):
        words = getattr(seg, "words", None) or []
        if not words:
            continue

        seg_corrections: list[WordCorrection] = []

        for w_idx, w in enumerate(words):
            word_text = getattr(w, "text", "") or ""
            confidence = getattr(w, "confidence", None)
            if confidence is None:
                continue

            match = _should_correct(
                word_text,
                confidence,
                threshold,
                standard_words,
                phonetic_index,
                standard_word_gate_active=standard_word_gate_active,
            )
            if match is None:
                continue
            term, edit_distance = match

            # Preserve surrounding punctuation, replace the core letters.
            leading, core, trailing = _strip_punctuation(word_text)
            replacement_word = f"{leading}{term.canonical}{trailing}"

            wc = WordCorrection(
                original=word_text,
                replacement=replacement_word,
                confidence_before=confidence,
                edit_distance=edit_distance,
                term_id=term.term_id,
                segment_index=seg_idx,
                word_index_in_segment=w_idx,
            )
            corrections.append(wc)
            seg_corrections.append(wc)

            # Apply in place.
            try:
                w.text = replacement_word
            except AttributeError:
                pass

        if seg_corrections:
            # Rebuild segment text from updated words.
            try:
                seg.text = " ".join((getattr(x, "text", "") or "") for x in words).strip()
            except AttributeError:
                pass
            # Annotate segment with corrections for UI consumption.
            existing = list(getattr(seg, "corrections", None) or [])
            existing.extend([
                {
                    "type": "domain_vocabulary",
                    "original": c.original,
                    "replacement": c.replacement,
                    "confidence_before": c.confidence_before,
                    "edit_distance": c.edit_distance,
                    "term_id": c.term_id,
                    "word_index": c.word_index_in_segment,
                }
                for c in seg_corrections
            ])
            try:
                seg.corrections = existing
            except AttributeError:
                pass

    return CorrectionResult(
        corrections=corrections,
        standard_word_gate_skipped=not standard_word_gate_active,
    )
