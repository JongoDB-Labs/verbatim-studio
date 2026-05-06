"""Auto-learn vocabulary terms from user corrections.

When a user manually edits a transcript word, we observe the
(original, replacement) pair and decide whether to add the replacement
to their custom dictionary so future transcripts get it right
automatically.

Hybrid of two industry approaches:

- **Wispr Flow's classifier-gated auto-add** — Wispr Flow detects proper
  nouns and adds them after the *first* user correction. The reasoning:
  if the user corrected a word into something that looks like a name or
  an acronym, the misrecognition will probably happen again, so it's
  worth adding immediately.
- **Descript's threshold-based auto-add** — Descript counts (original,
  replacement) pairs and auto-adds after **3 corrections of the same
  word**. The reasoning: high recall, low precision; 3 confirmations
  filter out one-off edits.

We compose them: clear proper nouns get the Wispr fast-path (auto-add
on first correction), ambiguous edits go through the Descript counter.

# What counts as "proper-noun-like"

A heuristic, not a model — we don't want to depend on Granite for a
synchronous edit hook because that would add hundreds of milliseconds
of LLM inference latency to every keystroke save. The heuristic flags
a replacement as proper-noun-like if any of these holds:

  1. **All-uppercase** with length ≥ 2 ("MCTSSA", "ADSEP", "USAF").
     Almost always an acronym in English speech.
  2. **PascalCase / TitleCase** AND not in the standard English
     wordlist ("Marforpac", "Antetokounmpo"). Rules out "Hello" /
     "Apple" — those are normal English. Captures invented proper
     nouns and product names.
  3. **Contains a digit** ("Q4-FY26", "AS-9100", "iPhone15"). Almost
     always a SKU / model number / fiscal-period style identifier.
  4. **Starts with capital and has internal punctuation** other than
     standard sentence ends ("U.S.A.", "MCT.SSA").

Each rule is intentionally conservative on its own; OR-ing them gives
high recall on the things we care about (acronyms, brands, names) and
low false-positive rate on normal English.

# When auto-learn is OFF

The settings flag `auto_learn_vocab` in post_transcription settings can
disable auto-learn entirely. Default: on. Users who never want this
behavior can switch it off and rely purely on the manual + CSV-import
paths.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Threshold for the Descript counter path: after this many user
# corrections of the same (original, replacement) pair, auto-add the
# replacement even if it's not classified as a proper noun.
DESCRIPT_THRESHOLD = 3


# ── Proper-noun classifier (heuristic, no LLM dependency) ────────────


_DIGIT_RE = re.compile(r"\d")
_NON_END_PUNCT_RE = re.compile(r"[^\w\s.!?]")


def _looks_like_proper_noun(replacement: str) -> str | None:
    """Classify *replacement*. Returns the rule name that fired, or None."""
    word = replacement.strip()
    if not word or len(word) < 2:
        return None

    # Rule 1: all-uppercase, length ≥ 2 — acronym.
    if word.isupper() and word.isalpha():
        return "all_uppercase"

    # Rule 3: contains a digit (and a letter, to skip pure numbers).
    if _DIGIT_RE.search(word) and any(c.isalpha() for c in word):
        return "alphanumeric_identifier"

    # Rule 4: starts with capital + has unusual punctuation.
    if word[0].isupper() and _NON_END_PUNCT_RE.search(word):
        return "punctuated_capitalized"

    # Rule 2: TitleCase / PascalCase and not standard English.
    if word[0].isupper() and word[1:].islower():
        try:
            from services.vocab_correction import _get_english_wordlist
            wordlist = _get_english_wordlist()
            if wordlist and word.lower() in wordlist:
                return None  # standard English word, e.g. "Hello"
            return "capitalized_nonstandard"
        except Exception:
            # Without the wordlist we can't gate cleanly. Fall back to a
            # length heuristic — words < 4 chars are mostly common words
            # that happen to start a sentence ("She", "And"); longer
            # capitalized words are usually proper nouns.
            if len(word) >= 4:
                return "capitalized_long"

    # Mixed case (PascalCase, camelCase) usually a proper noun or
    # product name (iPhone, MacBook, kubernetes).
    if any(c.isupper() for c in word[1:]) and any(c.islower() for c in word):
        return "mixed_case"

    return None


# ── Counter persistence (lightweight — no new table) ─────────────────


# We piggy-back on the existing custom_dictionary's `usage_count` for
# already-added terms, but we need a separate counter for not-yet-added
# (original, replacement) pairs. Simplest persistence: a JSON-backed
# Setting row keyed `vocab_auto_learn_counters`. Avoids another
# migration for what's effectively a coalescing counter.

_COUNTER_SETTING_KEY = "vocab_auto_learn_counters"


async def _get_counter_setting(db: AsyncSession) -> dict[str, int]:
    from persistence.models import Setting
    from sqlalchemy import select
    result = await db.execute(
        select(Setting).where(Setting.key == _COUNTER_SETTING_KEY)
    )
    row = result.scalar_one_or_none()
    if not row or not isinstance(row.value, dict):
        return {}
    return row.value


async def _set_counter_setting(db: AsyncSession, counters: dict[str, int]) -> None:
    from persistence.models import Setting
    from sqlalchemy import select
    result = await db.execute(
        select(Setting).where(Setting.key == _COUNTER_SETTING_KEY)
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = counters
    else:
        db.add(Setting(key=_COUNTER_SETTING_KEY, value=counters))


def _counter_key(original: str, replacement: str) -> str:
    return f"{original.lower().strip()}→{replacement.strip()}"


# ── Public entry points ──────────────────────────────────────────────


@dataclass
class AutoLearnOutcome:
    """What happened on a single learn-from-correction event."""

    learned: bool
    rule: str  # "proper_noun:<rule>", "threshold", or "skipped:<reason>"
    term_id: str | None  # set when learned == True


async def observe_correction(
    db: AsyncSession,
    *,
    original: str,
    replacement: str,
    project_id: str | None,
    auto_learn_enabled: bool = True,
) -> AutoLearnOutcome:
    """Record that the user changed *original* → *replacement*. Decide
    whether to auto-add *replacement* to the dictionary.

    Returns an AutoLearnOutcome describing what was done. Cheap to call
    on every segment edit — DB I/O on the counter setting is one row;
    proper-noun classification is regex + set membership.
    """
    if not auto_learn_enabled:
        return AutoLearnOutcome(learned=False, rule="skipped:disabled", term_id=None)

    original = (original or "").strip()
    replacement = (replacement or "").strip()
    if not original or not replacement or original.lower() == replacement.lower():
        return AutoLearnOutcome(learned=False, rule="skipped:no_change", term_id=None)

    # Single-word edits only — multi-word edits often conflate vocab
    # corrections with prose edits and aren't safe auto-learn fodder.
    if len(replacement.split()) > 1 or len(original.split()) > 1:
        return AutoLearnOutcome(learned=False, rule="skipped:multiword", term_id=None)

    # Already in the dictionary? Bump usage_count and exit.
    existing_id = await _existing_term_id(db, replacement, project_id)
    if existing_id:
        try:
            await db.execute(
                sql_text(
                    "UPDATE custom_dictionary SET usage_count = usage_count + 1 "
                    "WHERE id = :id"
                ),
                {"id": existing_id},
            )
        except Exception as e:
            logger.warning("Failed to bump usage_count on auto-learn observe: %s", e)
        return AutoLearnOutcome(learned=False, rule="skipped:already_in_dict", term_id=existing_id)

    # Wispr-style fast path: clear proper noun → auto-add immediately.
    rule = _looks_like_proper_noun(replacement)
    if rule:
        term_id = await _add_to_dictionary(db, replacement, project_id, source_rule=rule)
        return AutoLearnOutcome(learned=True, rule=f"proper_noun:{rule}", term_id=term_id)

    # Descript-style threshold path.
    counters = await _get_counter_setting(db)
    key = _counter_key(original, replacement)
    counters[key] = counters.get(key, 0) + 1
    if counters[key] >= DESCRIPT_THRESHOLD:
        # Auto-add and clear the counter.
        del counters[key]
        await _set_counter_setting(db, counters)
        term_id = await _add_to_dictionary(db, replacement, project_id, source_rule="threshold")
        return AutoLearnOutcome(learned=True, rule="threshold", term_id=term_id)

    await _set_counter_setting(db, counters)
    return AutoLearnOutcome(
        learned=False,
        rule=f"counting:{counters[key]}/{DESCRIPT_THRESHOLD}",
        term_id=None,
    )


async def _existing_term_id(
    db: AsyncSession, term: str, project_id: str | None
) -> str | None:
    if project_id:
        result = await db.execute(
            sql_text(
                "SELECT id FROM custom_dictionary "
                "WHERE LOWER(term) = :term_lower "
                "AND (project_id IS NULL OR project_id = :pid)"
            ),
            {"term_lower": term.lower(), "pid": project_id},
        )
    else:
        result = await db.execute(
            sql_text(
                "SELECT id FROM custom_dictionary "
                "WHERE LOWER(term) = :term_lower AND project_id IS NULL"
            ),
            {"term_lower": term.lower()},
        )
    row = result.fetchone()
    return row[0] if row else None


async def _add_to_dictionary(
    db: AsyncSession,
    term: str,
    project_id: str | None,
    *,
    source_rule: str,
) -> str:
    """Add the auto-learned term. Marked via category='auto_learned' so the
    frontend can render the 🔮 / ✨ indicator that distinguishes auto-
    added terms from user-curated ones (Wispr Flow's sparkle marker).
    Priority defaults to 0 (Normal); user can promote to Important later.
    """
    term_id = str(uuid.uuid4())
    try:
        await db.execute(
            sql_text(
                "INSERT INTO custom_dictionary "
                "(id, term, category, project_id, sounds_like, priority, usage_count) "
                "VALUES (:id, :term, 'auto_learned', :pid, NULL, 0, 1)"
            ),
            {"id": term_id, "term": term, "pid": project_id},
        )
        logger.info(
            "Auto-learned vocabulary term: %r (project=%s, rule=%s)",
            term, project_id or "global", source_rule,
        )
    except Exception as e:
        logger.warning("Auto-learn insert failed: %s", e)
        return ""
    return term_id
