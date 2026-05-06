"""LLM-based vocabulary correction (Phase 3).

A second-pass repair after Whisper produces a transcript. Catches the
long tail that prompt biasing (Phase 1) and gated phonetic correction
(Phase 2) miss — specifically:

- Multi-word terms where Whisper segmented words differently than the
  dictionary entry expects ("north star metric" → "northstar metric").
- Acronyms whose phonetic codes diverge from the spoken form because
  of letter-by-letter pronunciation (e.g. spoken "em-see-tee-tee-ess-ay"
  doesn't share Double Metaphone with "MCTSSA" as a string).
- Context-dependent disambiguation that pure phonetic match can't make
  ("operator" vs "operatour" — only the surrounding sentence resolves).

# Why local LLM, not remote API

Verbatim's transcripts often contain sensitive medical, legal, business
context. The "your audio never leaves your machine" promise is a core
product property. We use the bundled Granite (4-tiny / 4-h-tiny) via
the existing `IAIService` adapter rather than calling OpenAI/Anthropic.

# Why diff-bounded validation

The dominant LLM-correction failure mode (per the Whisper Courtside
paper, OpenAI Cookbook discussion, and our own experimentation): the
model produces a "free rewrite" that improves prose quality at the cost
of changing wording the user actually said. We protect against this by:

  1. Word-aligned diff: tokenize input + output, compute the LCS, only
     accept changes that fall into one of these categories:
       - Removal of a token where the replacement token is in the user's
         dictionary (or one of its sounds_like entries)
       - Pure punctuation/casing change
  2. Total word-count delta capped at ≤ 5%. Larger changes signal
     paraphrase, not correction.
  3. Per-segment processing: reject the entire segment's correction
     if validation fails — never accept a partially-validated rewrite.

# Why opt-in (default off)

LLM inference latency on Granite Tiny CPU is ~1-2 sec per segment. A
30-min recording with 200 segments could add 5-7 minutes of post-
processing. Gate behind a per-project / global setting so users opt in
when they need the long tail caught.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────


# Maximum allowed change in word count between input and LLM output for
# a given segment, expressed as a fraction of the input word count.
# Anything beyond this is paraphrase, not correction.
MAX_WORD_COUNT_DELTA = 0.05

# Hard cap regardless of fraction — for short segments (e.g. 5 words),
# 5% is 0.25 words, which rounds down to 0. We need at least 1 token of
# tolerance for "X" → "Y" repairs.
MIN_WORD_COUNT_DELTA_TOKENS = 2

# Segments shorter than this are skipped — too little context for the
# LLM to ground a correction usefully, and false-positive risk is higher.
MIN_SEGMENT_WORDS = 4

# Token tokenizer — split on whitespace + keep punctuation as separate
# tokens for alignment robustness.
_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


@dataclass
class LlmCorrection:
    """A single correction applied to a segment by the LLM pass."""

    original: str
    replacement: str
    segment_index: int


@dataclass
class LlmCorrectionResult:
    segments_processed: int = 0
    segments_modified: int = 0
    segments_rejected_too_diverged: int = 0
    segments_rejected_invalid_change: int = 0
    corrections: list[LlmCorrection] = field(default_factory=list)


# ── Prompt construction ──────────────────────────────────────────────


def _build_correction_prompt(
    segment_text: str,
    glossary_lines: list[str],
) -> str:
    """The prompt is intentionally restrictive.

    Pattern matches the OpenAI Whisper Cookbook's misspelling-correction
    recipe: a tight set of rules, the glossary, then the transcript. The
    explicit "Output the corrected transcript with NO commentary" line is
    important — Granite Tiny in particular likes to add explanatory text.
    """
    glossary = "\n".join(f"- {g}" for g in glossary_lines)
    return f"""You are a transcription correction assistant. Below is one segment from
an audio transcript that may contain misspellings of domain-specific terms.
A glossary of correct terms is provided.

Rules — these are NOT optional:
- ONLY fix proper-noun, acronym, or jargon misspellings using the glossary.
- DO NOT change wording, paraphrase, or rephrase.
- DO NOT change punctuation other than around a corrected term.
- DO NOT add or remove sentences.
- If a word in the transcript sounds similar to a glossary term AND the
  word is not a normal English word, replace it with the glossary term.
- Otherwise leave it alone — output it exactly as it appears.
- Output the corrected segment text with NO commentary, NO explanation,
  NO markdown. Just the corrected text.

Glossary:
{glossary}

Transcript segment:
{segment_text}

Corrected segment:"""


def _glossary_lines(entries: Iterable) -> list[str]:
    """Format dictionary entries for the LLM prompt.

    Speechmatics-style: include sounds_like alternates inline so the LLM
    knows what spellings might be present in the transcript that map to
    the canonical form.
    """
    lines: list[str] = []
    for e in entries:
        if e.sounds_like:
            sl = ", ".join(s for s in e.sounds_like if s)
            lines.append(f"{e.term} (also written as: {sl})")
        else:
            lines.append(e.term)
    return lines


# ── Diff-bounded validation ──────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def _word_count_delta_ok(input_words: int, output_words: int) -> bool:
    diff = abs(output_words - input_words)
    if diff <= MIN_WORD_COUNT_DELTA_TOKENS:
        return True
    if input_words == 0:
        return False
    return (diff / input_words) <= MAX_WORD_COUNT_DELTA


def _diff_is_glossary_only(
    input_text: str,
    output_text: str,
    glossary_terms_lower: set[str],
) -> bool:
    """Validate that every word change between input and output is a
    replacement *into* a glossary term (or a punctuation/case-only change).

    Algorithm: tokenize both, LCS-align, walk pairs:
      - identical (case-insensitive) tokens → fine
      - substitution (a[i] differs from b[j], both non-matching) → fine
        only if b[j] (the replacement) is a glossary term
      - pure deletion (a[i] removed, no corresponding addition) → fine
        only if a[i] is punctuation
      - pure insertion (b[j] added, no corresponding deletion) → fine
        only if b[j] is a glossary term or punctuation
    """
    a = _tokenize(input_text)
    b = _tokenize(output_text)

    # Length-bounded LCS (acceptable for transcript-segment scale).
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[i].lower() == b[j].lower():
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])

    i = j = 0
    while i < n and j < m:
        if a[i].lower() == b[j].lower():
            i += 1
            j += 1
            continue

        a_glossary = a[i].lower() in glossary_terms_lower
        b_glossary = b[j].lower() in glossary_terms_lower

        # Substitution detection: if both moves (delete-from-a vs.
        # insert-into-b) are equally optimal in the LCS table, this is
        # likely a substitution (replacement). Accept it when b[j] is a
        # glossary term — the LLM replaced a misspelling with the
        # correct term.
        delete_from_a_score = dp[i + 1][j]
        insert_into_b_score = dp[i][j + 1]

        if delete_from_a_score == insert_into_b_score:
            # Substitution — both have to advance.
            if b_glossary:
                i += 1
                j += 1
                continue
            return False

        if delete_from_a_score > insert_into_b_score:
            # Pure deletion of a[i]. Acceptable only if punctuation.
            if not a[i].isalnum():
                i += 1
                continue
            return False
        else:
            # Pure insertion of b[j]. Acceptable only if it's a
            # glossary term or punctuation.
            if b_glossary or not b[j].isalnum():
                j += 1
                continue
            return False
    # Anything trailing must be punctuation or a glossary term only.
    while i < n:
        if a[i].isalnum():
            return False
        i += 1
    while j < m:
        if b[j].isalnum() and b[j].lower() not in glossary_terms_lower:
            return False
        j += 1
    return True


# ── Public entry point ───────────────────────────────────────────────


async def llm_correct_segments(
    segments: list,
    dictionary_entries: list,
    ai_service,
    *,
    max_concurrency: int = 1,
) -> LlmCorrectionResult:
    """Run an LLM correction pass over segments.

    Args:
        segments: TranscriptionSegment objects (mutated in place).
        dictionary_entries: CustomDictionaryEntry objects.
        ai_service: An IAIService that provides .complete(prompt: str) -> str
                    or similar. We probe for the right method at call time
                    so we don't tightly couple to a specific adapter.
        max_concurrency: How many segments to process in parallel. Granite
                         on CPU is single-stream-bound, so default 1.

    Returns:
        LlmCorrectionResult with diagnostic counters + applied corrections.
    """
    result = LlmCorrectionResult()

    if not dictionary_entries or not segments:
        return result

    glossary_lines = _glossary_lines(dictionary_entries)
    if not glossary_lines:
        return result

    # Pre-compute lowercase glossary tokens for diff validation.
    glossary_terms_lower: set[str] = set()
    for e in dictionary_entries:
        glossary_terms_lower.add(e.term.lower())
        for sl in (e.sounds_like or []):
            glossary_terms_lower.add(sl.lower())

    sem = asyncio.Semaphore(max_concurrency)

    async def process_one(idx: int, seg) -> None:
        text = (getattr(seg, "text", "") or "").strip()
        if len(text.split()) < MIN_SEGMENT_WORDS:
            return
        result.segments_processed += 1
        prompt = _build_correction_prompt(text, glossary_lines)
        async with sem:
            try:
                output = await _invoke_ai(ai_service, prompt)
            except Exception as e:
                logger.warning("LLM correction failed on segment %d: %s", idx, e)
                return

        if output is None:
            return
        candidate = output.strip()
        # Strip a trailing "Corrected segment:" or similar leakage if any.
        if candidate.lower().startswith("corrected segment:"):
            candidate = candidate.split(":", 1)[-1].strip()
        if not candidate or candidate == text:
            return

        # Diff-bounded validation gates
        if not _word_count_delta_ok(
            len(_tokenize(text)), len(_tokenize(candidate))
        ):
            result.segments_rejected_too_diverged += 1
            return
        if not _diff_is_glossary_only(text, candidate, glossary_terms_lower):
            result.segments_rejected_invalid_change += 1
            return

        # Accepted. Apply.
        try:
            seg.text = candidate
            existing = list(getattr(seg, "corrections", None) or [])
            existing.append({
                "type": "llm_vocabulary",
                "original": text,
                "replacement": candidate,
            })
            seg.corrections = existing
        except AttributeError:
            pass
        result.corrections.append(LlmCorrection(
            original=text, replacement=candidate, segment_index=idx,
        ))
        result.segments_modified += 1

    await asyncio.gather(*[process_one(i, s) for i, s in enumerate(segments)])
    return result


async def _invoke_ai(ai_service, prompt: str) -> str | None:
    """Call the AI service's completion method.

    The IAIService surface varies — try common shapes in order.
    Returns the raw text, or None if no method works.
    """
    # complete(prompt) → str
    if hasattr(ai_service, "complete"):
        out = ai_service.complete(prompt)
        if asyncio.iscoroutine(out):
            out = await out
        return out
    # generate(prompt) → str
    if hasattr(ai_service, "generate"):
        out = ai_service.generate(prompt)
        if asyncio.iscoroutine(out):
            out = await out
        return out
    # chat([{"role":"user","content":prompt}]) → {"content": ...}
    if hasattr(ai_service, "chat"):
        msg = [{"role": "user", "content": prompt}]
        out = ai_service.chat(msg)
        if asyncio.iscoroutine(out):
            out = await out
        if isinstance(out, dict):
            return out.get("content") or out.get("text")
        return out
    raise RuntimeError(
        "ai_service has no recognized completion method (.complete, "
        ".generate, or .chat)"
    )
