# Domain Vocabulary Accuracy — Acronyms, Names, Brands

**Status:** Proposal
**Owner:** TBD
**Date:** 2026-05-06

## Problem

Whisper transcribes domain-specific terms incorrectly: "MCTSSA" → "mctissa", "ADSEP" → "adset". The same failure mode hits medical (drug names, anatomy), legal (case names, statutes), and business (product names, internal jargon) users. This is the single biggest quality complaint and applies to every customer with a non-generic vocabulary.

## What we already have

- `services/custom_dictionary.py` — DB-backed term list, project-scoped + global, builds a comma-separated `initial_prompt` truncated at 800 chars.
- `migrations/add_custom_dictionary.py` — `custom_dictionary` table.
- `adapters/transcription/whisperx.py` — passes `initial_prompt` to ctranslate2 in batch + streaming paths.
- `services/jobs.py` — batch handler loads dictionary, builds prompt, threads it through `TranscriptionOptions`.
- `core/interfaces/transcription.py:TranscriptionOptions.initial_prompt` — interface field already exists.
- WhisperX produces per-word `confidence` scores at `whisperx.py:321` — currently unused as a signal.

**Gaps:**
1. No frontend UI for managing dictionary entries.
2. No REST API for CRUD on the dictionary.
3. Live transcription path (`services/voice_agent.py:WhisperSTTAdapter.recognize()`) calls `transcribe()` with no options — voice chat ignores the dictionary entirely.
4. `services/quality_review.py` has no `domain_vocabulary` correction type.
5. `services/post_transcription_actions.py` has no vocabulary-fix action.
6. Prompt builder doesn't respect Whisper's 224-token budget (uses 800 chars), doesn't prioritize position (research shows end-of-prompt tokens carry the most weight), and uses a comma list (research shows natural prose outperforms comma lists for prompt biasing).
7. No `sounds_like` / phonetic hint per term — Speechmatics ships exactly this UX and it's the gold standard for users who can't spell IPA.

## How the big guys do it (TL;DR from research)

| Vendor | Mechanism | Cap | Notable |
|---|---|---|---|
| Otter | Custom Vocabulary | 100 names + 100 terms | Auto pronunciation derivation |
| Rev.ai | Glossary | ≤500 recommended | None |
| Descript | Glossary | 30/Drive | **Auto-learn after 3 corrections of same word** |
| Deepgram | Keywords + `intensifier` | 100 free | Continuous boost knob with "be careful" warning |
| AssemblyAI | `word_boost` | 1000 phrases × 6 words | Three-level boost (low/default/high) — deliberately coarse |
| Speechmatics | Custom Dictionary + `sounds_like` | ≤6 words/term | **Free-form phonetic respelling** ("nyohki" for gnocchi) |
| Azure | Phrase Lists | 500 | UPS phonetic notation |

Published WER numbers for techniques relevant to us:
- LLM post-correction (Courtside paper): **17.0% relative WER reduction** on proper-noun-dense transcripts.
- Contextual biasing fine-tunes (B-Whisper): **20-42% R-WER reduction** depending on dataset.
- TCPGen-style biasing: **45.6% rare-word, 60.8% unseen-word improvement.**
- Pure prompt biasing: qualitative only, no published WER number — but everyone ships it.

The cross-cutting tradeoff: **boost weight is dangerous**. Every continuous-knob vendor (Deepgram, Azure) has explicit docs warning that high boost causes false-positive insertions. AssemblyAI deliberately gates this to a 3-level enum. Pure phonetic match without confidence gating replaces normal English words with dictionary terms.

## Plan: layered, additive, default-safe

Ship in three phases. Each phase is shippable on its own and adds measurable accuracy without breaking what already works.

### Phase 1 — Make the existing dictionary actually usable (1 week)

**Goal:** users can add and use domain terms, and the existing prompt-biasing code respects token budgets and prioritization.

1. **REST API** — new router `api/routes/custom_dictionary.py` with:
   - `GET /api/dictionary?project_id=&category=` — list
   - `POST /api/dictionary` — create (term + optional category, optional project_id, optional `sounds_like`)
   - `PUT /api/dictionary/{id}` — edit
   - `DELETE /api/dictionary/{id}` — remove
   - `POST /api/dictionary/import` — CSV bulk import

2. **Schema migration** — extend `custom_dictionary` table:
   - Add `sounds_like TEXT` column (nullable, comma-separated alternate spellings — Speechmatics-style).
   - Add `priority INTEGER DEFAULT 0` column (used for prompt ordering — research shows end-of-prompt tokens get more attention).
   - Add `usage_count INTEGER DEFAULT 0` (for auto-learn metrics later).

3. **Frontend UI** — Settings → "Custom Vocabulary" page (or per-project tab):
   - Table: term, category, sounds_like, priority, scope (global vs. project), usage count
   - Add / edit / delete dialogs
   - CSV import button
   - Per-project toggle (in project settings) to enable/disable global dictionary
   - **No boost slider** — see decision below

4. **Better `build_initial_prompt`**:
   - Replace `max_chars=800` with `max_tokens=224` using `tiktoken` (Whisper uses GPT-2 BPE; `tiktoken.get_encoding("gpt2")` is a close-enough proxy and keeps us under the wall).
   - Sort entries by `(priority DESC, usage_count DESC)` so highest-priority terms land **last** in the prompt (research: tail tokens get more attention).
   - Switch from `term1, term2, term3` to natural-prose framing: prepend a short context sentence that mentions the highest-priority terms inline. Example: `"This is a transcript discussing MCTSSA, ADSEP, and Marforpac."` plus the remaining terms after. The cookbook explicitly recommends this framing.
   - Keep the comma list as the fallback for very large dictionaries.
   - Also pass the same string as `hotwords` to faster-whisper if the version supports it (no extra cost, slight tradeoff against `initial_prompt` per research).

5. **Wire live transcription**:
   - `services/voice_agent.py:WhisperSTTAdapter.recognize()` — accept project_id from session context, build `TranscriptionOptions` with `initial_prompt`, pass to `transcribe()`.
   - `api/routes/live.py` — if a session has a project context, load dictionary and pass it through.

**No boost slider** — the design decision: AssemblyAI's 3-level enum was the "what survives the false-positive tradeoff" answer, but even that's a pro-feature most users won't tune correctly. Default to "on, default weight," expose nothing in the UI for v1. If a power-user need emerges later, add it as an advanced toggle. Concretely: this is a deliberate choice to avoid shipping a knob that helps a few users and hurts most.

**Phase 1 acceptance:** user can add "MCTSSA" + sounds_like "em-see-tee-double-s-ay" to their dictionary, run a transcription, and see "MCTSSA" come out in the transcript. (Validation: pick 5 acronym-heavy recordings, measure recoveries before/after on a private benchmark.)

### Phase 2 — Gated phonetic post-correction (1 week)

**Goal:** even when prompt biasing fails (the term wasn't ranked highly enough, or the audio was unclear), repair it after the fact, without inventing wrong substitutions for normal English.

1. **New service `services/vocab_correction.py`** that runs after WhisperX completes:
   - Iterates per-word.
   - For each word, applies a 3-gate test:
     - (a) Whisper confidence below threshold (default 0.6 from per-word align scores — tune from data).
     - (b) Word **not** in a standard English dictionary (use the `pyenchant` or a frozen wordlist — small, offline).
     - (c) Phonetic match within edit-distance + Double Metaphone code match against the user's dictionary (including `sounds_like` alternates).
   - If all three fire: replace the word with the dictionary term, preserving original timing, and tag the segment with `corrections: [{type: "domain_vocab", original, replacement, confidence_before, confidence_after, term_id}]`.
   - Update the term's `usage_count`.

2. **Configurable thresholds** in settings:
   - "Auto-correct vocabulary" toggle (default: on)
   - "Confidence threshold" — single slider with three labelled stops (Conservative / Default / Aggressive) — same coarseness as AssemblyAI's three-level boost.

3. **Audit trail**:
   - Each transcript row gets a `vocab_corrections_json` column listing applied replacements.
   - User can review in the transcript viewer (small "✏️" icon next to corrected words; hover shows original) and click to revert.

4. **Quality review integration**:
   - Add `"domain_vocabulary"` to `quality_review.CorrectedSegment.correction_type`.
   - When the user runs quality review, also surface vocab corrections so they can be inspected together with hallucination/filler fixes.

**Phase 2 acceptance:** "MCTSSA" not boosted high enough to win on prompt biasing alone but with a low-confidence "mctissa" in the transcript → 3-gate correction finds it and fixes it. Confirm zero false positives on a clean English corpus (e.g. read a TED transcript with the user's MCTSSA dictionary loaded — nothing should change).

### Phase 3 — LLM cleanup pass (1-2 weeks)

**Goal:** catch the long tail that prompt biasing + phonetic correction miss. Specifically: terms used in context where the audio was clear but the BPE tokenization fragmented them, plus multi-word phrases that don't match phonetically because Whisper segmented them differently.

1. **Use the bundled local Granite LLM**, not a remote API. Privacy story stays clean (no transcript leaves the box).

2. **Opt-in "AI Cleanup" action** — runs as a `post_transcription_actions.VocabularyCorrectionAction`, only when the user enables it per project or globally (default: off in v1, may flip to on after measuring quality).

3. **Tight prompt:**
   ```
   Below is an audio transcript and a list of domain-specific terms the
   speaker may have used. Some of these terms may be misspelled in the
   transcript. Your job is ONLY to fix proper-noun and acronym
   misspellings using the glossary below. Do not change wording, do
   not paraphrase, do not change punctuation. If a word in the transcript
   is similar in sound to a term in the glossary AND the transcript
   word is not a normal English word, replace it. Otherwise leave it
   alone. Output the corrected transcript with NO commentary.

   Glossary: {dictionary_terms}

   Transcript:
   {transcript}
   ```

4. **Diff-bounded validation**:
   - Compare LLM output to original; if word-count delta > 5% or any single word change isn't either (a) in the glossary or (b) a punctuation/case change, reject the LLM output and keep the original.
   - This catches the "free rewrite" failure mode in the research.

5. **Word-level alignment preservation**:
   - Apply LLM corrections word-by-word using a Levenshtein-aligned diff so timing data stays attached. If an LLM correction would change word count for a segment, fall back to keeping that segment's original.

6. **Process per-segment, not whole-transcript** — keeps the LLM context manageable and limits the blast radius of a bad correction.

**Phase 3 acceptance:** measured WER improvement of 10%+ on a proper-noun-heavy benchmark relative to Phase 1+2. Approach the published 17% relative WER reduction from the Courtside paper, ideally clearing it because we're stacking with phonetic correction.

### Out of scope (deliberately)

- **Custom-trained Whisper fine-tunes per user.** The research shows this is the highest-accuracy approach (TCPGen, B-Whisper) but: requires per-user training data, ~hours of GPU compute, doesn't fit a desktop app. The B-Whisper or OWSM-Biasing models could be packaged as alternative weights, but that's a separate plan.
- **Boost weight UI.** Three vendors ship it, three vendors warn against it, and we don't have telemetry to give users data-backed defaults. Revisit when we have transcription quality metrics.
- **Pronunciation lexicon (UPS / IPA).** `sounds_like` free-form respelling is the user-friendlier form factor. Users who need IPA are a tiny minority.
- **Auto-learn from corrections.** Descript's nicest UX feature, but requires building the correction-tracking infra first. Add in v2 once the audit trail from Phase 2 is in place.

## Risks and what could go wrong

1. **Token-budget regression.** If we underestimate `tiktoken` for non-English, we'll silently truncate. Mitigation: log a warning when prompt is truncated, surface in dictionary UI as a "X of Y terms in active prompt" indicator.

2. **Phonetic-match false positives.** The 3-gate test is the safety net but the standard-English-word check is the load-bearing piece. We need a real wordlist (200k+ entries) and probably also the user's existing transcripts so common words there don't get flagged. Mitigation: ship with a frozen wordlist, allow user to add exceptions, default to conservative threshold.

3. **LLM rewrites valid speech.** The diff-bounded validation catches the worst cases but won't catch a glossary term replacing a similar-sounding normal word that the LLM rationalized. Mitigation: gate Phase 3 behind opt-in for v1, build telemetry on user revert rate.

4. **Live transcription latency.** Loading the dictionary on every chunk would add DB query overhead. Mitigation: cache the prompt per session at session start, refresh only on dictionary change.

5. **Backwards compatibility with existing recordings.** No issue — phonetic correction can run as a one-time backfill on user request via a "Re-correct vocabulary" button on each transcript.

## Decision points needing user input before we start

- **Phase priority confirmation.** Plan above is 1 → 2 → 3. Alternative: skip Phase 2, jump to Phase 3 (LLM-only). Tradeoff: Phase 3 alone needs Granite running, slower, opt-in only. Phase 2 first is safer and cheaper but more code.
- **Default-on vs default-off** for Phase 2's auto-correction. Recommendation: default-on with revert UI, because the 3-gate test is conservative.
- **Should we invest in a private benchmark dataset** before starting? Probably yes — without one we can't tell if Phase 3 actually helps. The user has real customer transcripts that could seed this.

## What's NOT being changed

- Existing batch transcription still works as-is during Phase 1; the prompt builder change is internal.
- Quality review's hallucination/filler/grammar correction continues to function.
- Voice chat behavior in v0.63.x remains unchanged until live transcription path is wired (Phase 1.5).

---

## References

- [OpenAI Whisper Prompting Guide](https://developers.openai.com/cookbook/examples/whisper_prompting_guide) — 224-token budget
- [OpenAI Cookbook: Whisper misspelling correction](https://developers.openai.com/cookbook/examples/whisper_correct_misspelling)
- [arXiv 2410.18363 — Contextual biasing without fine-tuning](https://arxiv.org/html/2410.18363v1) — end-of-prompt attention
- [arXiv 2502.11572 — Rare-word recognition](https://arxiv.org/html/2502.11572v1) — 45.6% / 60.8%
- [arXiv 2602.18966 — Whisper: Courtside Edition](https://arxiv.org/abs/2602.18966) — 17% LLM-pipeline gain
- [arXiv 2402.08021 — Careless Whisper](https://arxiv.org/html/2402.08021v2) — hallucination prevalence
- [Speechmatics Custom Dictionary](https://docs.speechmatics.com/features/custom-dictionary) — `sounds_like` UX
- [Deepgram Keywords](https://developers.deepgram.com/docs/keywords) — boost-weight tradeoff
- [AssemblyAI word_boost](https://www.assemblyai.com/docs/faq/how-can-i-make-certain-words-more-likely-to-be-transcribed) — 3-level enum design
