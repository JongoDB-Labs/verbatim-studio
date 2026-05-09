"""Hallucination filtering for live and post-hoc Whisper transcripts.

Whisper (and its variants) reliably hallucinate on silence, music, low-SNR
audio, and short chunks. The hallucinations follow predictable patterns —
training-data artifacts like "Thanks for watching!" or repeated single
words. This module centralizes the filter so live mode, voice chat, and
chunked file transcription all share the same logic.

Public API:
    is_hallucination(text, *, confidence=None, duration=None,
                     prev_text=None) -> bool

Returns True when *text* is most likely a Whisper hallucination and the
caller should drop the segment.
"""

from __future__ import annotations

import re

# ABSOLUTE phrases — never legitimate speech. Always drop when matched,
# regardless of duration/confidence. These are training-set artifacts that
# Whisper invents from silence/music/noise.
_ABSOLUTE_HALLUCINATION_PHRASES: frozenset[str] = frozenset({
    # YouTube/captioning artifacts
    "thanks for watching",
    "thank you for watching",
    "thanks for watching the video",
    "thanks for watching this video",
    "please subscribe",
    "like and subscribe",
    "like comment and subscribe",
    "don't forget to like and subscribe",
    "don't forget to subscribe",
    "subscribe to my channel",
    "subscribe to the channel",
    "click the bell icon",
    "hit the bell icon",
    "smash the like button",
    "see you in the next video",
    "i'll see you in the next video",
    "see you in the next one",
    "see you guys in the next one",
    "thanks for listening",
    "thank you for listening",
    "thanks so much for watching",
    # Captioning credit artifacts
    "subtitles by the amara.org community",
    "subtitles by the amara.org",
    "amara.org",
    "transcribed by",
    "transcript by",
    "translation by",
    "captions by",
    # Captioning brackets and music tags
    "[music]",
    "[applause]",
    "[silence]",
    "[no audio]",
    "[laughter]",
    "[laughs]",
    "[inaudible]",
    "[crosstalk]",
    "(music)",
    "(applause)",
    "(silence)",
    "♪",
    "♫",
    "♪♪",
    "♬",
})

# WEAK phrases — commonly hallucinated but ALSO legitimate speech.
# Drop only if the segment is also short (<0.6s) or low-confidence
# (<-0.7). A real "thank you" usually has decent confidence and ~0.5s+
# duration, while a hallucinated "thank you" is almost always either
# too short or too uncertain.
_WEAK_HALLUCINATION_PHRASES: frozenset[str] = frozenset({
    "you",
    "thank you",
    "thank you very much",
    "thanks",
    "bye",
    "bye bye",
    "goodbye",
    "okay",
    "ok",
    "yeah",
    "yep",
    "mhm",
    "mm-hmm",
    "uh-huh",
    "uh huh",
    "oh",
    "ah",
    "huh",
    "wow",
    "music",
    "applause",
})

# Keep _HALLUCINATION_PHRASES as the union for any external consumers.
_HALLUCINATION_PHRASES: frozenset[str] = (
    _ABSOLUTE_HALLUCINATION_PHRASES | _WEAK_HALLUCINATION_PHRASES
)

# Strip leading/trailing punctuation (incl. unicode ellipsis) and whitespace
_STRIP_RE = re.compile(r"^[\s\.\!\?\,…\[\]\(\)\-]+|[\s\.\!\?\,…\[\]\(\)\-]+$")

# Patterns where a few words repeat over and over (common low-SNR pattern)
_REPETITIVE_WORD_RE = re.compile(r"\b(\w{2,})\b(?:\s+\1\b){2,}", re.IGNORECASE)

# Confidence threshold — Whisper avg_logprob below this is essentially noise
_CONFIDENCE_NOISE_THRESHOLD = -1.0
# Confidence threshold for treating a WEAK match as a hallucination
_WEAK_CONFIDENCE_THRESHOLD = -0.7
# Duration threshold below which any speech is suspect
_MIN_REAL_DURATION = 0.3
# Duration threshold for treating a WEAK match as a hallucination when
# confidence is missing
_WEAK_DURATION_THRESHOLD = 0.6


def _normalize(text: str) -> str:
    """Lowercase, strip surrounding punctuation/whitespace, collapse spaces."""
    text = _STRIP_RE.sub("", text or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def is_hallucination(
    text: str,
    *,
    confidence: float | None = None,
    duration: float | None = None,
    prev_text: str | None = None,
) -> bool:
    """Decide whether *text* is most likely a Whisper hallucination.

    Args:
        text: The candidate transcript text.
        confidence: Optional avg log-prob (lower is worse). Whisper segments
            below ~-1.0 are essentially noise.
        duration: Optional segment duration in seconds. Segments < 0.3s
            are too short to contain meaningful speech.
        prev_text: Optional text of the previous segment. If this segment
            is an exact repeat of the previous one, treat as a stuck-loop
            hallucination (Whisper sometimes echoes when audio is silent).
    """
    if text is None:
        return True

    raw = text.strip()
    if not raw:
        return True

    # Drop ultra-short segments — under ~0.3s no real word fits
    if duration is not None and duration < _MIN_REAL_DURATION:
        return True

    # Drop very low confidence — Whisper avg_logprob below -1.0 is noise
    if confidence is not None and confidence < _CONFIDENCE_NOISE_THRESHOLD:
        return True

    norm = _normalize(raw)
    if not norm:
        return True

    # Stuck-loop detection: identical to previous segment
    if prev_text and _normalize(prev_text) == norm:
        return True

    # ABSOLUTE phrases — drop unconditionally
    if norm in _ABSOLUTE_HALLUCINATION_PHRASES:
        return True

    # Phrase-prefix match for ABSOLUTE: "thanks for watching" inside
    # "thanks for watching everyone"
    for phrase in _ABSOLUTE_HALLUCINATION_PHRASES:
        if len(phrase) >= 12 and (norm == phrase or norm.startswith(phrase + " ")):
            return True

    # WEAK phrases — only drop if also short OR low-confidence. Otherwise
    # legitimate "thank you", "yeah", "ok" survive.
    if norm in _WEAK_HALLUCINATION_PHRASES:
        weak_short = duration is not None and duration < _WEAK_DURATION_THRESHOLD
        weak_low_conf = (
            confidence is not None and confidence < _WEAK_CONFIDENCE_THRESHOLD
        )
        weak_unknown = duration is None and confidence is None
        if weak_short or weak_low_conf or weak_unknown:
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
    prev_text: str | None = None
    for seg in segments:
        text = seg.get(text_key, "")
        conf = seg.get(confidence_key)
        start = seg.get(start_key)
        end = seg.get(end_key)
        duration = (end - start) if (start is not None and end is not None) else None
        if not is_hallucination(
            text,
            confidence=conf,
            duration=duration,
            prev_text=prev_text,
        ):
            out.append(seg)
            prev_text = text
    return out
