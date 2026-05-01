"""Hallucination filtering for live and post-hoc Whisper transcripts.

Whisper (and its variants) reliably hallucinate on silence, music, low-SNR
audio, and short chunks. The hallucinations follow predictable patterns —
training-data artifacts like "Thanks for watching!" or repeated single
words. This module centralizes the filter so live mode, voice chat, and
chunked file transcription all share the same logic.

Public API:
    is_hallucination(text, *, confidence=None, duration=None) -> bool

Returns True when *text* is most likely a Whisper hallucination and the
caller should drop the segment.
"""

from __future__ import annotations

import re

# Phrases Whisper emits on silence/music. Lowercased for comparison.
# Trailing punctuation is stripped before matching.
_HALLUCINATION_PHRASES: frozenset[str] = frozenset({
    # YouTube/captioning artifacts
    "thanks for watching",
    "thank you for watching",
    "thanks for watching!",
    "please subscribe",
    "like and subscribe",
    "don't forget to subscribe",
    "see you in the next video",
    "i'll see you in the next video",
    "see you next time",
    "subtitles by the amara.org community",
    "subtitles by the amara.org",
    "amara.org",
    "transcribed by",
    # Captioning brackets
    "[music]",
    "[applause]",
    "[silence]",
    "[no audio]",
    "music",
    "applause",
    # Generic short hallucinations
    "you",
    "thank you",
    "bye",
    "bye!",
    "bye bye",
    "goodbye",
    "okay",
    "ok",
    "hmm",
    "uh",
    "um",
    "...",
    ". . .",
})

# Strip leading/trailing punctuation (incl. unicode ellipsis) and whitespace
_STRIP_RE = re.compile(r"^[\s\.\!\?\,…\[\]\(\)\-]+|[\s\.\!\?\,…\[\]\(\)\-]+$")

# Patterns where a few words repeat over and over (common low-SNR pattern)
_REPETITIVE_WORD_RE = re.compile(r"\b(\w{2,})\b(?:\s+\1\b){2,}", re.IGNORECASE)


def _normalize(text: str) -> str:
    """Lowercase, strip surrounding punctuation/whitespace, collapse spaces."""
    text = _STRIP_RE.sub("", text or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def is_hallucination(
    text: str,
    *,
    confidence: float | None = None,
    duration: float | None = None,
) -> bool:
    """Decide whether *text* is most likely a Whisper hallucination.

    Args:
        text: The candidate transcript text.
        confidence: Optional avg log-prob (lower is worse). Whisper segments
            below ~-1.0 are essentially noise.
        duration: Optional segment duration in seconds. Segments < 0.3s
            are too short to contain meaningful speech.
    """
    if text is None:
        return True

    raw = text.strip()
    if not raw:
        return True

    # Drop ultra-short segments — under ~0.3s no real word fits
    if duration is not None and duration < 0.3:
        return True

    # Drop very low confidence — Whisper avg_logprob below -1.0 is noise
    if confidence is not None and confidence < -1.0:
        return True

    norm = _normalize(raw)
    if not norm:
        return True

    # Direct match against known canned phrases
    if norm in _HALLUCINATION_PHRASES:
        return True

    # Phrase-prefix match: "thanks for watching" inside "thanks for watching!"
    for phrase in _HALLUCINATION_PHRASES:
        if len(phrase) >= 12 and (norm == phrase or norm.startswith(phrase + " ")):
            return True

    # Limited alphabet artifacts: "aaaa", "ababababab", etc.  Requires
    # enough characters to confidently distinguish from a short legitimate
    # word like "hi" or "ok".
    chars_only = re.sub(r"[\s\.\!\?,]", "", norm)
    if len(chars_only) >= 4 and len(set(chars_only)) <= 2:
        return True

    words = norm.split()
    if len(words) >= 4:
        # Same word/2-word phrase repeating: "yeah yeah yeah yeah"
        unique = set(words)
        if len(unique) <= 2:
            return True
        # Regex for explicit immediate repetition
        if _REPETITIVE_WORD_RE.search(norm):
            # Fire only if the repetition covers most of the text
            match = _REPETITIVE_WORD_RE.search(norm)
            if match and len(match.group(0)) > len(norm) * 0.6:
                return True

    return False


def filter_segments(
    segments: list[dict],
    *,
    text_key: str = "text",
    confidence_key: str = "confidence",
    start_key: str = "start",
    end_key: str = "end",
) -> list[dict]:
    """Drop hallucinated segments from a list of transcript dicts.

    Args:
        segments: Segment dicts with at least *text_key*. Optional
            *confidence_key* and *start_key*/*end_key* enable additional
            heuristics.
        text_key, confidence_key, start_key, end_key: Field names in
            each dict.
    """
    out: list[dict] = []
    for seg in segments:
        text = seg.get(text_key, "")
        conf = seg.get(confidence_key)
        start = seg.get(start_key)
        end = seg.get(end_key)
        duration = (end - start) if (start is not None and end is not None) else None
        if not is_hallucination(text, confidence=conf, duration=duration):
            out.append(seg)
    return out
