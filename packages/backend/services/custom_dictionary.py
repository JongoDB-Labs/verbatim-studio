"""Custom dictionary service for domain-specific transcription accuracy.

Manages user-defined terms that are passed to Whisper's `initial_prompt`
parameter, biasing the model toward specific words and phrases (e.g.
technical jargon, proper nouns, medical terms, military acronyms).

# How the prompt gets built

Whisper's documented hard limit is **224 tokens** of prompt context, not
characters. We use tiktoken's GPT-2 encoder (Whisper's actual tokenizer)
to measure budget exactly. Going over silently truncates with no error.

# Prompt ordering

Per recent contextual-biasing research (arXiv 2410.18363), Whisper's
encoder attention biases toward the *end* of the prompt. So highest-
priority terms must land last. We sort by (priority DESC, usage_count
DESC) and emit in *reverse* of that order — lowest-priority first,
highest-priority last — so the most important terms get the most weight.

# Prompt framing

OpenAI's Whisper Prompting Guide explicitly recommends natural prose
("Aimee and Shawn ate whisky, doughnuts, omelets at a BBQ") over bare
comma lists. We emit a one-sentence intro mentioning the highest-priority
terms inline, then comma-list the remainder. This style-matches the model
to a transcript-prose register and outperforms a raw list.

# `sounds_like`

Modelled on Speechmatics' UX. Free-form alternate spellings (no IPA) the
user provides for tricky pronunciations (e.g. "nyohki, nyokey" for
"gnocchi"). For prompt biasing, we expose them inline alongside the term
so Whisper has multiple lexical hooks. For phonetic post-correction
(Phase 2), they double the match keys per term.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Whisper's documented prompt budget. Going past silently truncates.
WHISPER_PROMPT_TOKEN_LIMIT = 224

# Conservative default — leaves headroom for the natural-prose intro framing.
# When estimation/encoding is unavailable we fall back to a char proxy at
# ~3.2 chars/token (English BPE average).
DEFAULT_PROMPT_CHAR_BUDGET = 700


@dataclass
class CustomDictionaryEntry:
    """A single dictionary entry."""

    term: str
    category: str = "general"  # tech, medical, legal, names, general
    project_id: str | None = None  # None = global, otherwise project-specific
    sounds_like: list[str] = field(default_factory=list)
    priority: int = 0  # higher = lands later in prompt = more attention
    usage_count: int = 0
    id: str | None = None
    created_at: str | None = None


# ── Token measurement ─────────────────────────────────────────────────


def _count_tokens(text_to_measure: str) -> int:
    """Count tokens using GPT-2 BPE (Whisper's tokenizer family).

    Falls back to a char/3.2 estimate if tiktoken is unavailable. The
    fallback is conservative — chars/3.2 overestimates token count for
    most English, so we err toward truncating slightly more than needed
    rather than blowing past 224.
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("gpt2")
        return len(enc.encode(text_to_measure))
    except Exception:
        return max(1, int(len(text_to_measure) / 3.2))


# ── Prompt construction ──────────────────────────────────────────────


def _filter_and_dedupe(
    entries: list[CustomDictionaryEntry],
    project_id: str | None,
) -> list[CustomDictionaryEntry]:
    """Pick the entries that apply to this transcription, deduped case-insensitively."""
    filtered: list[CustomDictionaryEntry] = []
    for entry in entries:
        if entry.project_id is None:
            filtered.append(entry)  # global — always
        elif project_id is not None and entry.project_id == project_id:
            filtered.append(entry)
    if not filtered:
        return []

    seen: set[str] = set()
    unique: list[CustomDictionaryEntry] = []
    for entry in filtered:
        key = entry.term.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def _ordered_for_prompt(entries: list[CustomDictionaryEntry]) -> list[CustomDictionaryEntry]:
    """Order so high-priority lands LAST. Whisper attends most to tail tokens.

    Sort key: (priority ASC, usage_count ASC, term length ASC). Then the
    highest-priority and most-used terms emit last and get the most attention.
    """
    return sorted(
        entries,
        key=lambda e: (e.priority, e.usage_count, len(e.term)),
    )


def _format_term_with_alternates(entry: CustomDictionaryEntry) -> str:
    """Render a term plus its sounds_like alternates inline.

    Format: "MCTSSA (also: em-see-tee-double-s-ay)". Whisper sees both
    spellings and links the audio to the canonical form. If no alternates,
    just the term.
    """
    if not entry.sounds_like:
        return entry.term
    alternates = ", ".join(s for s in entry.sounds_like if s.strip())
    if not alternates:
        return entry.term
    return f"{entry.term} (also: {alternates})"


def build_initial_prompt(
    entries: list[CustomDictionaryEntry],
    project_id: str | None = None,
    max_tokens: int = WHISPER_PROMPT_TOKEN_LIMIT,
    *,
    intro_template: str = "This recording uses domain-specific terms including {top_terms}.",
) -> str | None:
    """Build a Whisper initial_prompt from dictionary entries.

    Args:
        entries: All dictionary entries (will be filtered + deduped + ordered).
        project_id: If set, include project-specific entries for this project
                    in addition to global entries.
        max_tokens: Token budget — Whisper's documented limit is 224 and
                    going past silently truncates. Default
                    WHISPER_PROMPT_TOKEN_LIMIT.
        intro_template: Prose-framing template. Whisper responds better to
                        natural-language framing than a bare comma list. The
                        {top_terms} slot gets the 3 highest-priority terms
                        inline. Pass an empty string to skip the intro for
                        a pure comma list.

    Returns:
        Prompt string, or None if no entries match.
    """
    relevant = _filter_and_dedupe(entries, project_id)
    if not relevant:
        return None

    ordered = _ordered_for_prompt(relevant)
    # Highest-priority terms (the last few in the ordering) get featured in
    # the intro sentence too — mention is cheap and reinforces the bias.
    featured = ordered[-3:] if len(ordered) >= 3 else ordered
    featured_terms = [e.term for e in reversed(featured)]  # highest first in intro

    intro = ""
    if intro_template and featured_terms:
        intro_terms = ", ".join(featured_terms)
        intro = intro_template.format(top_terms=intro_terms)

    # Build the trailing comma-list, in priority-ascending order so the
    # highest-priority terms land at the very end of the prompt where
    # encoder attention is strongest.
    rendered_terms = [_format_term_with_alternates(e) for e in ordered]

    # Greedy fit-into-budget. Token-measure the running candidate and stop
    # when adding another term would exceed max_tokens. We measure the full
    # candidate each iteration because BPE merges depend on context (a
    # term's token count differs based on what's adjacent).
    accepted_terms: list[str] = []

    def render(terms: list[str]) -> str:
        body = ", ".join(terms)
        if intro and body:
            return f"{intro} {body}"
        if intro:
            return intro
        return body

    for term_str in rendered_terms:
        candidate = render(accepted_terms + [term_str])
        if _count_tokens(candidate) > max_tokens:
            break
        accepted_terms.append(term_str)

    if not accepted_terms and not intro:
        return None
    return render(accepted_terms)


# ── DB queries ────────────────────────────────────────────────────────


async def load_dictionary_entries(
    db: AsyncSession | None = None,
    project_id: str | None = None,
) -> list[CustomDictionaryEntry]:
    """Load dictionary entries from the database.

    Args:
        db: An async SQLAlchemy session. When called from the job thread
            (which doesn't have a request-scoped session), pass None and
            the function will create its own session.
        project_id: If set, also load project-specific entries for that
                    project (global entries are always loaded).

    Returns:
        List of CustomDictionaryEntry objects, raw order from DB.
        Callers that need ordering should pass through build_initial_prompt
        or order themselves.
    """
    if db is None:
        from persistence.database import get_session_factory
        async with get_session_factory()() as session:
            return await _query_entries(session, project_id)
    return await _query_entries(db, project_id)


def _parse_sounds_like(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


async def _query_entries(
    session: AsyncSession,
    project_id: str | None = None,
) -> list[CustomDictionaryEntry]:
    """Query custom_dictionary table, returning entries.

    Tolerates the v1 schema (no sounds_like / priority / usage_count) by
    catching the missing-column error and falling back to a v1 query.
    Once everyone is on v2 the fallback can be removed.
    """
    try:
        if project_id:
            result = await session.execute(
                text(
                    "SELECT id, term, category, project_id, created_at, "
                    "sounds_like, priority, usage_count "
                    "FROM custom_dictionary "
                    "WHERE project_id IS NULL OR project_id = :pid "
                    "ORDER BY priority DESC, usage_count DESC, created_at"
                ),
                {"pid": project_id},
            )
        else:
            result = await session.execute(
                text(
                    "SELECT id, term, category, project_id, created_at, "
                    "sounds_like, priority, usage_count "
                    "FROM custom_dictionary "
                    "WHERE project_id IS NULL "
                    "ORDER BY priority DESC, usage_count DESC, created_at"
                )
            )
        rows = result.fetchall()
        return [
            CustomDictionaryEntry(
                id=row[0],
                term=row[1],
                category=row[2],
                project_id=row[3],
                created_at=row[4],
                sounds_like=_parse_sounds_like(row[5]),
                priority=row[6] or 0,
                usage_count=row[7] or 0,
            )
            for row in rows
        ]
    except Exception as e:
        logger.warning("v2 custom_dictionary query failed (%s) — falling back to v1 schema", e)
        try:
            if project_id:
                result = await session.execute(
                    text(
                        "SELECT id, term, category, project_id, created_at "
                        "FROM custom_dictionary "
                        "WHERE project_id IS NULL OR project_id = :pid "
                        "ORDER BY created_at"
                    ),
                    {"pid": project_id},
                )
            else:
                result = await session.execute(
                    text(
                        "SELECT id, term, category, project_id, created_at "
                        "FROM custom_dictionary "
                        "WHERE project_id IS NULL "
                        "ORDER BY created_at"
                    )
                )
            rows = result.fetchall()
            return [
                CustomDictionaryEntry(
                    id=row[0],
                    term=row[1],
                    category=row[2],
                    project_id=row[3],
                    created_at=row[4],
                )
                for row in rows
            ]
        except Exception as inner:
            logger.warning("Could not query custom_dictionary table: %s", inner)
            return []


async def increment_usage(
    db: AsyncSession,
    term_ids: list[str],
) -> None:
    """Increment usage_count for the given dictionary term ids.

    Called after a transcript has been finalized and we can attribute
    detected terms to specific dictionary rows. Used as a secondary sort
    key in build_initial_prompt and as the seed for the eventual
    Descript-style auto-learn-after-N-corrections feature.
    """
    if not term_ids:
        return
    try:
        await db.execute(
            text(
                "UPDATE custom_dictionary "
                "SET usage_count = usage_count + 1 "
                "WHERE id IN :ids"
            ).bindparams(ids=tuple(term_ids)),
        )
    except Exception as e:
        logger.warning("Failed to increment usage_count: %s", e)
