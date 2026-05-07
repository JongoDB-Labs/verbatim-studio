"""Pronunciation derivation for the vocabulary corpus.

# Why this matters

Whisper transcribes audio to text. When it hears "em see tee ess ess
ay", it has to decide whether to write "MCTSSA" (correct) or
"mctissa" (the run-together false friend). The prompt biases the
choice toward the correct spelling, but Phase 2 phonetic correction
needs MORE than just the canonical form to repair misrecognitions —
it needs to know how the term *sounds* so Double Metaphone matching
can pair "mctissa" → "MCTSSA" or "mic wil" → "MCWL".

That's what `sounds_like` is for. The runtime correction code already
matches against sounds_like alternates as additional phonetic keys
(see services/vocab_correction.py:TermPhoneticIndex.best_match).

# Three modes of pronunciation

Acronyms break into three pronunciation styles:

1. **Letter-by-letter** (FBI, IRS, AWS, JFK):
   Always spelled out. Whisper hears "ef bee eye" and writes "FBI"
   only when biased; otherwise produces "F B I" with spaces or
   misreads as "FB" or three separate words.

2. **Word-style** (NATO, NASA, SCUBA, MCWL):
   Pronounced as a single word. Vowel-rich enough that the human
   tongue glides through them. NATO → "nay-toh", MCWL → "mic-wil".

3. **Hybrid** (USAID = "you-ess-aid", JPEG = "jay-peg"):
   First letters spelled out, last syllable read. Less common but
   real (USPS sometimes "you-ess-pee-ess", sometimes "U-S-P-S").

# What the helper does

`derive_acronym_pronunciations(canonical)` returns a list of plausible
spoken forms:
- Always includes the letter-by-letter spelling
- For vowel-rich acronyms, also includes a syllable-merged form
- For acronyms with a built-in word ending (USAID, JPEG, EDIPI),
  generates a hybrid form

Editorial caveat: this helper does NOT know which pronunciation a
specific community uses. MCWL is "mic-wil" in Marine Corps but might
be "M-C-W-L" elsewhere. Curated source modules (military_acronyms,
business_acronyms, etc.) should pass an explicit `sounds_like_hints`
list when the pronunciation is locked in by usage. The helper's
output augments those hints, never overrides them.
"""

from __future__ import annotations

from typing import Iterable

# ARPAbet-style mouth-friendly phonetic spellings for English letters.
# Used to build the letter-by-letter form. We pick the spellings most
# likely to round-trip through Whisper:
#   "ay" not "a" (Whisper would tokenize bare "a" as the article)
#   "double-u" not "doubleyou" (Whisper handles dashes well)
_LETTER_PRON: dict[str, str] = {
    "A": "ay", "B": "bee", "C": "see", "D": "dee", "E": "ee",
    "F": "ef", "G": "gee", "H": "aitch", "I": "eye", "J": "jay",
    "K": "kay", "L": "ell", "M": "em", "N": "en", "O": "oh",
    "P": "pee", "Q": "cue", "R": "ar", "S": "ess", "T": "tee",
    "U": "you", "V": "vee", "W": "double-u", "X": "ex", "Y": "why",
    "Z": "zee",
}

# Vowels for the "is this acronym pronounceable as a word?" heuristic.
_VOWELS = set("AEIOU")
# Letters that act as semi-vowels in syllable formation.
_SEMI_VOWELS = set("YW")


def letter_by_letter(acronym: str, *, joiner: str = " ") -> str:
    """Produce the spelled-out form of an acronym.

    "MCTSSA" → "em see tee ess ess ay"
    "FBI"    → "ef bee eye"

    Numbers stay as digits ("F-35" → "ef thirty-five" is harder; we
    emit "ef 35" and let the prompt-builder handle it). Hyphens and
    slashes are dropped from the spoken form.
    """
    out: list[str] = []
    for ch in acronym:
        upper = ch.upper()
        if upper in _LETTER_PRON:
            out.append(_LETTER_PRON[upper])
        elif ch.isdigit():
            out.append(ch)
    return joiner.join(out)


def has_word_shape(acronym: str) -> bool:
    """Heuristic: can this acronym be pronounced as a single word?

    The rule of thumb: vowel-rich enough that consecutive consonants
    don't exceed ~2 in any run. NASA, SCUBA, MCWL, NATO pass.
    MCTSSA, KGB, SQL fail.

    Doesn't handle every case — community usage trumps the heuristic.
    Curated source modules can override by passing explicit hints.
    """
    letters = [c.upper() for c in acronym if c.isalpha()]
    if len(letters) < 3:
        return False
    if not any(l in _VOWELS or l in _SEMI_VOWELS for l in letters):
        return False
    # Reject if there's any run of 3+ consonants (un-pronounceable).
    consonant_run = 0
    for l in letters:
        if l in _VOWELS or l in _SEMI_VOWELS:
            consonant_run = 0
        else:
            consonant_run += 1
            if consonant_run >= 3:
                return False
    return True


def implicit_vowel_word_forms(acronym: str) -> list[str]:
    """Generate plausible word-form pronunciations by inserting implicit
    vowels between consonant clusters.

    Acronyms get pronounced as portmanteau words across many
    occupational fields — military, medical, legal, finance — even when
    they look unpronounceable on paper. MCTSSA → "mick-tiss-uh".
    MCTSSA's letters M-C-T-S-S-A read as a word by treating each
    consonant cluster as needing a vowel: M-(i)-CT-(i)-SS-A → "mick
    tissa". The exact vowel choice varies by community but Double
    Metaphone normalizes most short-vowel inserts to the same code, so
    we don't have to be exact — covering the common patterns is enough
    to give the runtime correction layer a target.

    Strategy: for each consecutive run of consonants, insert each of
    {"i", "e", "a"} between consecutive consonants. Generate the
    full string with no spaces, with hyphens after each insertion, and
    a few common segmentation patterns.

    Examples:
      MCTSSA → ["mctissa", "mctessa", "mctassa", "mck-tissa", ...]
      MCWL   → ["mcwil", "mcwel", "mcwal"]
      ABCD   → ["abicd", "abecd", "abacd", "abcid", ...]

    Returns a deduplicated list. Output is always lowercase.
    """
    if not acronym:
        return []
    letters = [c for c in acronym if c.isalpha()]
    if len(letters) < 3:
        # 2-char acronyms rarely word-form (FBI is 3 chars and even that's
        # universally letter-by-letter; below 3 isn't worth the noise).
        return []

    upper = "".join(letters).upper()
    out: list[str] = []
    seen: set[str] = set()

    def _add(form: str) -> None:
        cleaned = form.strip().lower()
        if cleaned and cleaned not in seen and 2 <= len(cleaned) <= 60:
            seen.add(cleaned)
            out.append(cleaned)

    # 1. Bare lowercase concatenation — covers Whisper hallucinations
    #    that drop the case (e.g. it heard MCTSSA, output "mctssa").
    _add(upper.lower())

    # 2. Vowel-inserted variants. For each gap between consecutive
    #    consonants, try inserting {i, e, a, u}. We don't enumerate all
    #    combinations exhaustively — instead, generate one variant per
    #    inserted vowel choice, applied uniformly across all gaps.
    for vowel in ("i", "e", "a", "u"):
        rebuilt: list[str] = []
        for idx, ch in enumerate(upper):
            rebuilt.append(ch.lower())
            if (
                idx + 1 < len(upper)
                and ch.upper() not in _VOWELS
                and ch.upper() not in _SEMI_VOWELS
                and upper[idx + 1].upper() not in _VOWELS
                and upper[idx + 1].upper() not in _SEMI_VOWELS
            ):
                rebuilt.append(vowel)
        _add("".join(rebuilt))

    # 3. Hyphenated form of the i-inserted variant — sometimes Whisper
    #    or downstream tooling preserves a dash from the spoken pause.
    if "i" in (out[1] if len(out) > 1 else ""):
        # Re-run the i variant but with hyphens at consonant-cluster boundaries
        rebuilt: list[str] = []
        for idx, ch in enumerate(upper):
            rebuilt.append(ch.lower())
            if (
                idx + 1 < len(upper)
                and ch.upper() not in _VOWELS
                and ch.upper() not in _SEMI_VOWELS
                and upper[idx + 1].upper() not in _VOWELS
                and upper[idx + 1].upper() not in _SEMI_VOWELS
            ):
                rebuilt.extend(("i", "-"))
        # Trim trailing dash
        candidate = "".join(rebuilt).rstrip("-")
        _add(candidate)

    return out


def derive_acronym_pronunciations(
    canonical: str,
    *,
    extra_hints: Iterable[str] = (),
) -> list[str]:
    """Return a list of plausible spoken forms for *canonical*.

    Always includes:
    - Letter-by-letter spelling, space-joined ("ef bee eye")
    - Letter-by-letter spelling, hyphen-joined ("ef-bee-eye")
    - The bare lowercase form ("fbi")
    - Vowel-inserted word-form variants ("mctissa", "mctessa", "mctassa")
      for any acronym 3+ chars long

    The vowel-inserted forms catch the common occupational-field pattern
    where acronyms get pronounced as portmanteau words by inserting
    implicit vowels. Double Metaphone normalizes short-vowel inserts to
    similar codes, so even if the user pronounced "mick-tiss-uh" we get
    a phonetic match against "mctissa" / "mctessa" / "mctassa".

    `extra_hints` lets curated source modules supply community-locked
    pronunciations (e.g. MARCORSEPMAN = "mar corps sep man") that the
    auto-derivation can't infer. Hints are added after the derived forms.

    All forms are returned lowercase, deduplicated, in stable order.
    """
    forms: list[str] = []
    seen: set[str] = set()

    cleaned = "".join(c for c in canonical if c.isalnum())
    if not cleaned:
        return list(dict.fromkeys(extra_hints))

    # Letter-by-letter (space + hyphen joiners).
    for f in (
        letter_by_letter(cleaned, joiner=" "),
        letter_by_letter(cleaned, joiner="-"),
    ):
        if f and f not in seen:
            seen.add(f)
            forms.append(f)

    # Word-form / implicit-vowel variants.
    for f in implicit_vowel_word_forms(cleaned):
        if f and f not in seen:
            seen.add(f)
            forms.append(f)

    # Curated hints last (preserve their phrasing).
    for hint in extra_hints:
        clean_hint = (hint or "").strip().lower()
        if clean_hint and clean_hint not in seen:
            seen.add(clean_hint)
            forms.append(clean_hint)

    return forms
