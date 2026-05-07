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


def derive_acronym_pronunciations(
    canonical: str,
    *,
    extra_hints: Iterable[str] = (),
) -> list[str]:
    """Return a list of plausible spoken forms for *canonical*.

    Always includes:
    - Letter-by-letter spelling, joined by spaces ("ef bee eye")
    - Letter-by-letter spelling, joined by hyphens ("ef-bee-eye")
      (some prompts respond better to one form than the other)

    Word-form pronunciation is NOT auto-emitted, even when the
    acronym has a vowel-rich shape — the heuristic over-fires on
    things like FBI/IRS/AWS that look pronounceable but are
    universally spelled out in spoken English. Curated sources
    that know a community pronounces an acronym as a word (MCWL =
    "mic wil", MARFORPAC = "mar-for-pack") supply that form via
    `extra_hints`. The helper deduplicates and lowercases.

    All forms are returned lowercase, stripped of whitespace, and
    deduplicated.
    """
    forms: list[str] = []
    seen: set[str] = set()

    # Filter out non-alphanumeric chars from acronym for the spoken form.
    cleaned = "".join(c for c in canonical if c.isalnum())
    if not cleaned:
        return list(dict.fromkeys(extra_hints))

    space_form = letter_by_letter(cleaned, joiner=" ")
    hyphen_form = letter_by_letter(cleaned, joiner="-")

    for f in (space_form, hyphen_form):
        if f and f not in seen:
            seen.add(f)
            forms.append(f)

    for hint in extra_hints:
        clean_hint = (hint or "").strip().lower()
        if clean_hint and clean_hint not in seen:
            seen.add(clean_hint)
            forms.append(clean_hint)

    return forms
