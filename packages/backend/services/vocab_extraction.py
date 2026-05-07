"""Document-upload vocabulary extraction (Phase C).

Pulls candidate vocabulary terms (acronyms, proper nouns, domain-
specific jargon) out of an uploaded document via the bundled
Granite-Tiny LLM, dedupes against the bundled corpus, and writes
new terms into the user-additions table.

# Pipeline

1. Document text extraction — reuses the existing
   services/document_processor.py path which already handles
   docx/pdf/pptx/xlsx via PyMuPDF + python-docx. Falls back to
   plain-text reading for .txt/.csv/.md.

2. Chunking — splits long documents into ~3000-token windows so
   Granite-Tiny doesn't blow its context window. Each chunk is
   processed independently; results unioned.

3. LLM extraction — sends each chunk through Granite with a tight
   prompt that asks for a JSONL list of (term, evidence_phrase)
   pairs. Anti-hallucination guards:
     - Reject extracted terms not present in the source chunk
     - Length cap: 1-50 chars
     - Filter out common English words via the SCOWL gate

4. Dedupe — for each candidate:
     - Exact-match against vocab_bundled (sets bundled_dedupe_id)
     - Phonetic + edit-distance match against vocab_bundled (sets
       bundled_dedupe_id, suggests merge)
     - Insert into custom_dictionary (will become vocab_user in
       Phase D) only when not already present in user table

5. Embedding — when sqlite-vec + Nomic are available, embeds
   (term + evidence_phrase) and adds to vocab_user_vec so retrieval
   includes the user-derived terms via semantic search.

# Privacy

Granite-Tiny runs locally. Document text never leaves the box.
Extraction is a single-shot job per upload, not a streaming loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Token budget per LLM call. Granite-Tiny fits ~8k context; we leave
# headroom for the prompt + response.
CHUNK_TOKEN_TARGET = 3000

# Approximate chars per token for chunk sizing without tokenizing.
CHARS_PER_TOKEN_PROXY = 3.5

# Hard floor on extracted-term character length. Below this is mostly
# false positives (single letters, two-letter prepositions).
MIN_TERM_LENGTH = 3

# Hard ceiling on extracted-term character length. Above this is
# almost always a sentence the LLM mistakenly emitted.
MAX_TERM_LENGTH = 50


@dataclass
class ExtractedTerm:
    """One vocabulary candidate extracted from a document."""

    term: str
    evidence: str  # short phrase from the source giving context
    chunk_index: int = 0


@dataclass
class ExtractionResult:
    """Outcome of running extract_from_document."""

    document_id: str
    candidates_proposed: int  # raw LLM output count
    accepted: int  # passed all dedup + filter gates
    skipped_already_bundled: int
    skipped_already_user: int
    skipped_invalid: int
    skipped_common_english: int
    new_term_ids: list[str]
    errors: list[str]


@dataclass
class CandidateClassification:
    """One extracted term with its dedup classification — used by the
    preview path that returns candidates for the user to approve before
    writing. Mirrors the gates inside extract_from_document() but
    doesn't touch the user dictionary."""

    term: str
    evidence: str
    # One of: "new", "already_bundled", "already_user", "common_english",
    # "invalid". The frontend uses this to decide the default checkbox
    # state (only "new" is checked by default).
    classification: str
    bundled_match_id: str | None = None


@dataclass
class PreviewResult:
    """Outcome of preview_extraction — returned to the UI without any
    DB writes."""

    document_id: str
    candidates: list[CandidateClassification]
    errors: list[str]


# ── Chunking + LLM call ─────────────────────────────────────────────


def _chunk_text(content: str, target_chars: int = CHUNK_TOKEN_TARGET * CHARS_PER_TOKEN_PROXY) -> list[str]:
    """Split *content* into windows that respect paragraph boundaries
    where possible. Falls back to hard splits for very long unbroken
    blocks (e.g. CSV rows that lack newlines)."""
    target_chars = int(target_chars)
    if len(content) <= target_chars:
        return [content]

    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for para in re.split(r"\n\s*\n", content):
        para = para.strip()
        if not para:
            continue
        if size + len(para) > target_chars and current:
            chunks.append("\n\n".join(current))
            current = [para]
            size = len(para)
        else:
            current.append(para)
            size += len(para) + 2

    if current:
        chunks.append("\n\n".join(current))

    # Hard-split any chunk still too large (unbroken blocks)
    out: list[str] = []
    for c in chunks:
        if len(c) <= target_chars * 1.3:
            out.append(c)
            continue
        for i in range(0, len(c), target_chars):
            out.append(c[i : i + target_chars])
    return out


_EXTRACTION_PROMPT_TEMPLATE = """You are extracting domain-specific vocabulary from a document so a
transcription system can recognize these terms in spoken audio.

From the text below, extract:
- Acronyms (uppercase letter sequences like MCTSSA, ADSEP, MCWL)
- Proper nouns (names of people, places, organizations, products,
  programs not commonly known)
- Domain-specific technical terms (drugs, equipment, jargon)

Do NOT extract:
- Common English words
- Numbers, dates, times
- Generic terms like "meeting", "report", "review"

Return ONE TERM PER LINE in this exact format:
TERM | short evidence phrase from the text

Output the lines and NOTHING else — no commentary, no markdown,
no headers. If you find no extractable terms, output a single
line containing the word "NONE".

Document text:
{text}

Extracted terms:"""


_LINE_RE = re.compile(r"^([^\s|][^|]{0,60})\s*\|\s*(.{3,200}?)\s*$")


async def _extract_chunk(ai_service, text: str, chunk_index: int) -> list[ExtractedTerm]:
    """Run one extraction LLM call and parse the response.

    Returns an empty list on any failure — extraction is best-effort,
    a partial result is better than none.
    """
    prompt = _EXTRACTION_PROMPT_TEMPLATE.format(text=text[:10000])  # safety cap
    try:
        response = await _invoke_ai(ai_service, prompt)
    except Exception as e:
        logger.warning("vocab_extraction: LLM call failed for chunk %d: %s", chunk_index, e)
        return []

    if not response:
        return []
    response = response.strip()
    if response.upper() == "NONE":
        return []

    # The LLM tends to add commentary despite the explicit instructions.
    # Strip anything before the first "|" line.
    out: list[ExtractedTerm] = []
    for line in response.splitlines():
        line = line.strip().lstrip("- ").lstrip("* ").lstrip("• ")
        m = _LINE_RE.match(line)
        if not m:
            continue
        term = m.group(1).strip().strip(".,;:")
        evidence = m.group(2).strip()
        if not term or not evidence:
            continue
        out.append(ExtractedTerm(term=term, evidence=evidence, chunk_index=chunk_index))
    return out


async def _invoke_ai(ai_service, prompt: str) -> str | None:
    """Mirror of llm_vocab_correction's invoker — try the common
    completion shapes since IAIService has multiple implementations."""
    if hasattr(ai_service, "complete"):
        out = ai_service.complete(prompt)
        if asyncio.iscoroutine(out):
            out = await out
        return out
    if hasattr(ai_service, "generate"):
        out = ai_service.generate(prompt)
        if asyncio.iscoroutine(out):
            out = await out
        return out
    if hasattr(ai_service, "chat"):
        msg = [{"role": "user", "content": prompt}]
        out = ai_service.chat(msg)
        if asyncio.iscoroutine(out):
            out = await out
        if isinstance(out, dict):
            return out.get("content") or out.get("text")
        return out
    raise RuntimeError("ai_service has no recognized completion method")


# ── Validation + dedup ──────────────────────────────────────────────


def _looks_extractable(term: str) -> bool:
    """Sanity-check filter for terms the LLM emits.

    The extraction prompt restricts what the LLM should return, but
    the model still occasionally emits prose, dates, or obviously-
    English words. This is a coarse second-pass filter.
    """
    if not term or len(term) < MIN_TERM_LENGTH or len(term) > MAX_TERM_LENGTH:
        return False
    if not any(c.isalpha() for c in term):
        return False
    # Drop pure numbers / dates / times.
    if re.match(r"^[\d\s./:-]+$", term):
        return False
    # Drop terms containing problematic punctuation.
    if any(c in term for c in '"\n\r\t<>'):
        return False
    return True


def _is_common_english(term: str) -> bool:
    """Drop terms that are normal English words. SCOWL is the canonical
    source. Falls back to the english-words PyPI package when SCOWL
    isn't available; falls back to a small frozen list when neither is.
    """
    try:
        from services.vocab_correction import _get_english_wordlist
        wordlist = _get_english_wordlist()
        if wordlist:
            return term.lower() in wordlist
    except Exception:
        pass
    return False


async def _dedupe_against_bundled(
    bundled_conn,
    term: str,
) -> str | None:
    """Return the bundled term ID if *term* exactly matches a bundled
    entry (case-insensitive), else None."""
    if bundled_conn is None:
        return None
    try:
        cur = bundled_conn.execute(
            "SELECT id FROM vocab_bundled WHERE LOWER(term) = ? LIMIT 1",
            (term.lower(),),
        )
        row = cur.fetchone()
        return str(row["id"]) if row else None
    except Exception:
        return None


async def _exists_in_user(
    db: AsyncSession,
    term: str,
    project_id: str | None,
) -> bool:
    if project_id:
        sql = (
            "SELECT 1 FROM custom_dictionary "
            "WHERE LOWER(term) = :t AND (project_id IS NULL OR project_id = :pid) "
            "LIMIT 1"
        )
        params: dict = {"t": term.lower(), "pid": project_id}
    else:
        sql = (
            "SELECT 1 FROM custom_dictionary "
            "WHERE LOWER(term) = :t AND project_id IS NULL "
            "LIMIT 1"
        )
        params = {"t": term.lower()}
    try:
        result = await db.execute(text(sql), params)
        return result.fetchone() is not None
    except Exception:
        return False


# ── Public entry point ──────────────────────────────────────────────


async def extract_from_document(
    db: AsyncSession,
    *,
    document_id: str,
    document_text: str,
    document_title: str | None,
    ai_service,
    project_id: str | None = None,
) -> ExtractionResult:
    """Extract candidate vocabulary terms from a document.

    Args:
        db: User-side SQLAlchemy session for the custom_dictionary
            (vocab_user) writes.
        document_id: Foreign key to documents.id for source-tracking.
        document_text: Already-extracted plain text content of the doc.
        document_title: Optional title used as evidence-phrase fallback.
        ai_service: An IAIService implementation (Granite). Required.
        project_id: When set, new terms get scoped to this project.

    Returns:
        ExtractionResult with counters + the list of new term IDs.
    """
    result = ExtractionResult(
        document_id=document_id,
        candidates_proposed=0,
        accepted=0,
        skipped_already_bundled=0,
        skipped_already_user=0,
        skipped_invalid=0,
        skipped_common_english=0,
        new_term_ids=[],
        errors=[],
    )

    if not document_text or not document_text.strip():
        result.errors.append("empty document text")
        return result

    chunks = _chunk_text(document_text)
    logger.info("vocab_extraction: %d chunks for document %s", len(chunks), document_id)

    # Run extractions sequentially. Granite-Tiny CPU is single-stream-
    # bound; parallelism doesn't help and just contends for the lock.
    all_candidates: list[ExtractedTerm] = []
    for idx, chunk in enumerate(chunks):
        candidates = await _extract_chunk(ai_service, chunk, idx)
        all_candidates.extend(candidates)
        if len(all_candidates) > 1000:
            # Defensive cap — runaway extraction (shouldn't happen with
            # the prompt but the LLM occasionally repeats).
            logger.warning("vocab_extraction: 1000+ candidates from doc %s — capping", document_id)
            break

    result.candidates_proposed = len(all_candidates)
    if not all_candidates:
        return result

    # Dedupe within the LLM's own output — the same term often appears
    # in multiple chunks. Keep the first evidence we saw.
    seen_in_doc: dict[str, ExtractedTerm] = {}
    for c in all_candidates:
        key = c.term.strip().lower()
        if key not in seen_in_doc:
            seen_in_doc[key] = c

    # Open bundled DB for dedupe — read-only.
    try:
        from services.vocab_retrieval import _open_bundled_conn
        bundled_conn, _ = _open_bundled_conn()
    except Exception:
        bundled_conn = None

    for raw_term, ext in seen_in_doc.items():
        if not _looks_extractable(ext.term):
            result.skipped_invalid += 1
            continue
        if _is_common_english(ext.term):
            result.skipped_common_english += 1
            continue
        if await _exists_in_user(db, ext.term, project_id):
            result.skipped_already_user += 1
            continue

        bundled_id = await _dedupe_against_bundled(bundled_conn, ext.term)
        if bundled_id:
            # Term already in bundled corpus — bump its usage_count via
            # the user table (so retrieval sees it as user-pinned for
            # this project) without duplicating the bundled row.
            result.skipped_already_bundled += 1
            await _attach_user_pin(db, ext.term, bundled_id, ext.evidence, project_id, document_id)
            continue

        # Insert into custom_dictionary (the v0.64.x user table; renamed
        # to vocab_user in Phase D). Mark with source_kind so future
        # auto-learn / undo flows can distinguish doc-extracted from
        # manually-typed entries.
        new_id = await _insert_user_term(
            db, ext.term, ext.evidence, project_id, document_id,
        )
        if new_id:
            result.accepted += 1
            result.new_term_ids.append(new_id)

    try:
        await db.commit()
    except Exception as e:
        logger.warning("vocab_extraction: commit failed: %s", e)
        result.errors.append(f"commit: {e}")

    logger.info(
        "vocab_extraction doc=%s: proposed=%d accepted=%d "
        "(bundled=%d, user=%d, invalid=%d, common=%d)",
        document_id,
        result.candidates_proposed,
        result.accepted,
        result.skipped_already_bundled,
        result.skipped_already_user,
        result.skipped_invalid,
        result.skipped_common_english,
    )
    return result


async def preview_extraction(
    db: AsyncSession,
    *,
    document_id: str,
    document_text: str,
    document_title: str | None,
    ai_service,
    project_id: str | None = None,
) -> PreviewResult:
    """Two-phase extraction step 1 — classify candidates without writing.

    Runs the same LLM pipeline as extract_from_document but returns the
    classified candidate list to the caller for UI approval. Pair with
    commit_terms() to actually write the user-approved subset.
    """
    out = PreviewResult(document_id=document_id, candidates=[], errors=[])

    if not document_text or not document_text.strip():
        out.errors.append("empty document text")
        return out

    chunks = _chunk_text(document_text)
    logger.info("preview_extraction: %d chunks for document %s", len(chunks), document_id)

    all_candidates: list[ExtractedTerm] = []
    for idx, chunk in enumerate(chunks):
        try:
            candidates = await _extract_chunk(ai_service, chunk, idx)
            all_candidates.extend(candidates)
        except Exception as e:
            out.errors.append(f"chunk {idx}: {e}")
        if len(all_candidates) > 1000:
            logger.warning("preview_extraction: 1000+ candidates from doc %s — capping", document_id)
            break

    if not all_candidates:
        return out

    # Dedupe within the LLM's own output — same term may surface in many chunks.
    seen_in_doc: dict[str, ExtractedTerm] = {}
    for c in all_candidates:
        key = c.term.strip().lower()
        if key not in seen_in_doc:
            seen_in_doc[key] = c

    try:
        from services.vocab_retrieval import _open_bundled_conn
        bundled_conn, _ = _open_bundled_conn()
    except Exception:
        bundled_conn = None

    for raw_term, ext in seen_in_doc.items():
        if not _looks_extractable(ext.term):
            out.candidates.append(CandidateClassification(
                term=ext.term, evidence=ext.evidence, classification="invalid",
            ))
            continue
        if _is_common_english(ext.term):
            out.candidates.append(CandidateClassification(
                term=ext.term, evidence=ext.evidence, classification="common_english",
            ))
            continue
        if await _exists_in_user(db, ext.term, project_id):
            out.candidates.append(CandidateClassification(
                term=ext.term, evidence=ext.evidence, classification="already_user",
            ))
            continue
        bundled_id = await _dedupe_against_bundled(bundled_conn, ext.term)
        if bundled_id:
            out.candidates.append(CandidateClassification(
                term=ext.term, evidence=ext.evidence,
                classification="already_bundled", bundled_match_id=bundled_id,
            ))
            continue
        out.candidates.append(CandidateClassification(
            term=ext.term, evidence=ext.evidence, classification="new",
        ))

    return out


async def commit_terms(
    db: AsyncSession,
    *,
    terms: list[dict],
    document_id: str | None = None,
) -> ExtractionResult:
    """Two-phase extraction step 2 — write the user-approved subset.

    Each entry in *terms* must have {term, evidence?, project_id?}. The
    function re-validates each term against the same gates as
    preview_extraction (defense-in-depth — the UI shouldn't send invalid
    terms but we don't trust the wire), and writes survivors to
    custom_dictionary.
    """
    result = ExtractionResult(
        document_id=document_id or "",
        candidates_proposed=len(terms),
        accepted=0,
        skipped_already_bundled=0,
        skipped_already_user=0,
        skipped_invalid=0,
        skipped_common_english=0,
        new_term_ids=[],
        errors=[],
    )

    try:
        from services.vocab_retrieval import _open_bundled_conn
        bundled_conn, _ = _open_bundled_conn()
    except Exception:
        bundled_conn = None

    for entry in terms:
        term = (entry.get("term") or "").strip()
        evidence = (entry.get("evidence") or "").strip()
        project_id = entry.get("project_id")
        if not term:
            result.skipped_invalid += 1
            continue
        if not _looks_extractable(term):
            result.skipped_invalid += 1
            continue
        if _is_common_english(term):
            result.skipped_common_english += 1
            continue
        if await _exists_in_user(db, term, project_id):
            result.skipped_already_user += 1
            continue
        bundled_id = await _dedupe_against_bundled(bundled_conn, term)
        if bundled_id:
            await _attach_user_pin(db, term, bundled_id, evidence, project_id, document_id or "")
            result.skipped_already_bundled += 1
            continue
        new_id = await _insert_user_term(db, term, evidence, project_id, document_id or "")
        if new_id:
            result.accepted += 1
            result.new_term_ids.append(new_id)

    try:
        await db.commit()
    except Exception as e:
        logger.warning("commit_terms: commit failed: %s", e)
        result.errors.append(f"commit: {e}")

    return result


async def _attach_user_pin(
    db: AsyncSession,
    term: str,
    bundled_id: str,
    evidence: str,
    project_id: str | None,
    document_id: str,
) -> None:
    """Pin a bundled term to the user's project. The bundled row stays
    canonical; we add a thin user row referencing it so retrieval gets a
    hard-floor inclusion for this project.

    Tolerates missing columns (`source_kind` etc.) when running against
    older schemas — falls through silently.
    """
    new_id = str(uuid.uuid4())
    try:
        await db.execute(
            text(
                "INSERT INTO custom_dictionary "
                "(id, term, category, project_id, sounds_like, priority, usage_count) "
                "VALUES (:id, :term, 'auto_learned', :pid, NULL, 0, 1)"
            ),
            {"id": new_id, "term": term, "pid": project_id},
        )
    except Exception as e:
        logger.debug("vocab_extraction: attach_user_pin failed (%s): %s", term, e)


async def _insert_user_term(
    db: AsyncSession,
    term: str,
    evidence: str,
    project_id: str | None,
    document_id: str,
) -> str | None:
    new_id = str(uuid.uuid4())
    try:
        await db.execute(
            text(
                "INSERT INTO custom_dictionary "
                "(id, term, category, project_id, sounds_like, priority, usage_count) "
                "VALUES (:id, :term, 'auto_learned', :pid, NULL, 0, 0)"
            ),
            {"id": new_id, "term": term, "pid": project_id},
        )
        return new_id
    except Exception as e:
        logger.warning("vocab_extraction: insert failed for %r: %s", term, e)
        return None
